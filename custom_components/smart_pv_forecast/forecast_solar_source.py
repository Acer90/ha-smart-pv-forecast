"""forecast.solar API client for PV production estimates.

Uses the public https://api.forecast.solar/estimate endpoint (no API key required)
or the authenticated endpoint when an API key is configured.

API rate limits:
  - Public (no key): 12 requests per hour
  - Personal/Personal+/Professional: higher limits (see forecast.solar docs)

The API already returns a fully computed PV estimate (W, Wh) for the given
plane parameters; unlike DWD/Open-Meteo there is no raw radiation step.
One API call is made per PV array; results are summed to the combined estimate.

Azimuth convention:
  - Home Assistant uses 0..360 (0/360 = North, 90 = East, 180 = South, 270 = West)
  - forecast.solar uses -180..180 (0 = South, -90 = East, 90 = West, ±180 = North)
  - Conversion: fs_azimuth = ha_azimuth - 180
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiohttp import ClientSession, ClientResponseError

_LOGGER = logging.getLogger(__name__)

_BASE_URL = "https://api.forecast.solar"
_RATE_LIMIT_WARNING_THRESHOLD = 3


class ForecastSolarRateLimitError(Exception):
    """Raised when the forecast.solar API returns HTTP 429."""


class ForecastSolarClient:
    """Fetches PV production estimates from the forecast.solar API."""

    def __init__(self, session: ClientSession, api_key: str = "") -> None:
        self._session = session
        self._api_key = api_key.strip()

    async def fetch_estimate(
        self,
        *,
        latitude: float,
        longitude: float,
        pv_arrays: list[dict[str, Any]],
        shading_factor_fn: Callable[[int, datetime], float] | None = None,
    ) -> "ForecastSolarEstimate":
        """Fetch and combine estimates for all given PV arrays.

        pv_arrays: list of dicts with keys: peak (Wp), azimuth (0-360°), tilt (0-90°)
        Returns a ForecastSolarEstimate with combined watts/wh_period/wh_days.
        """
        if not pv_arrays:
            raise ValueError("No PV arrays configured for forecast.solar")

        per_array: list[_ArrayEstimate] = []
        baseline: list[dict[datetime, float]] = []
        for index, array in enumerate(pv_arrays):
            ae = await self._fetch_one_array(
                latitude=array.get("latitude", latitude),
                longitude=array.get("longitude", longitude),
                tilt=array["tilt"],
                azimuth_ha=array["azimuth"],
                peak_wp=array["peak"],
            )
            baseline.append(dict(ae.wh_period))
            if shading_factor_fn is not None:
                ae.wh_period = {
                    ts: value * shading_factor_fn(index, ts)
                    for ts, value in ae.wh_period.items()
                }
                ae.wh_days = {}
                for ts, value in ae.wh_period.items():
                    day = ts.replace(hour=0, minute=0, second=0, microsecond=0)
                    ae.wh_days[day] = ae.wh_days.get(day, 0.0) + value
            per_array.append(ae)

        combined = _merge_array_estimates(per_array)
        combined.array_wh_period = baseline
        return combined

    async def _fetch_one_array(
        self,
        *,
        latitude: float,
        longitude: float,
        tilt: float,
        azimuth_ha: float,
        peak_wp: float,
    ) -> "_ArrayEstimate":
        """Make one API call for a single plane and return parsed array estimate."""
        dec = int(round(max(0, min(90, tilt))))
        az = int(round((azimuth_ha % 360) - 180))
        kwp = round(peak_wp / 1000.0, 4)

        if self._api_key:
            url = (
                f"{_BASE_URL}/{self._api_key}/estimate"
                f"/{latitude}/{longitude}/{dec}/{az}/{kwp}"
            )
        else:
            url = f"{_BASE_URL}/estimate/{latitude}/{longitude}/{dec}/{az}/{kwp}"

        try:
            async with self._session.get(url) as resp:
                if resp.status == 429:
                    raise ForecastSolarRateLimitError(
                        "forecast.solar rate limit exceeded (HTTP 429)"
                    )
                resp.raise_for_status()
                data: dict[str, Any] = await resp.json()
        except ClientResponseError as exc:
            raise ValueError(f"forecast.solar HTTP error: {exc.status}") from exc

        return _parse_array_response(data)


# ---------------------------------------------------------------------------
# Internal data structures
# ---------------------------------------------------------------------------


class _ArrayEstimate:
    """Parsed per-array estimate from a single forecast.solar response."""

    __slots__ = ("timezone", "wh_period", "wh_days")

    def __init__(
        self,
        timezone: str,
        wh_period: dict[datetime, float],
        wh_days: dict[datetime, float],
    ) -> None:
        self.timezone = timezone
        self.wh_period = wh_period
        self.wh_days = wh_days


class ForecastSolarEstimate:
    """Combined estimate across all PV arrays, ready for use in the coordinator."""

    __slots__ = ("timezone", "watts", "wh_period", "wh_days", "array_wh_period")

    def __init__(
        self,
        timezone: str,
        watts: dict[datetime, float],
        wh_period: dict[datetime, float],
        wh_days: dict[datetime, float],
    ) -> None:
        self.timezone = timezone
        self.watts = watts
        self.wh_period = wh_period
        self.wh_days = wh_days
        self.array_wh_period: list[dict[datetime, float]] = []


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def _parse_array_response(data: dict[str, Any]) -> _ArrayEstimate:
    """Parse one forecast.solar JSON response into an _ArrayEstimate.

    Bins sub-hourly timestamps into full-hour buckets so the result is
    compatible with the Estimate model (which expects hourly keys).

    Timezone is read from message.timezone (top-level) or message.info.timezone
    (fallback), as the API field location varies between response types.
    """
    result = data.get("result") or {}
    message = data.get("message") or {}
    info = message.get("info") or {}

    # message.timezone is the primary field; message.info.timezone is a fallback
    tz_str = (
        str(message.get("timezone"))
        if message.get("timezone")
        else str(info.get("timezone") or "UTC")
    )
    _LOGGER.debug("forecast.solar detected timezone: %s", tz_str)
    try:
        tz = ZoneInfo(tz_str)
    except (ZoneInfoNotFoundError, Exception):
        _LOGGER.warning(
            "forecast.solar: unknown timezone %r, falling back to UTC – "
            "forecast times will be incorrect!",
            tz_str,
        )
        tz = ZoneInfo("UTC")
        tz_str = "UTC"

    ratelimit = message.get("ratelimit") or {}
    remaining = ratelimit.get("remaining")
    if remaining is not None:
        try:
            remaining_int = int(remaining)
            if remaining_int <= _RATE_LIMIT_WARNING_THRESHOLD:
                _LOGGER.warning(
                    "forecast.solar rate limit nearly exhausted: %d requests remaining",
                    remaining_int,
                )
        except (TypeError, ValueError):
            pass

    wh_period_raw: dict[str, Any] = result.get("watt_hours_period") or {}

    # Bin watt_hours_period into full-hour buckets (sum sub-hourly values).
    wh_period: dict[datetime, float] = {}
    for ts_str, val in wh_period_raw.items():
        try:
            dt = datetime.strptime(str(ts_str), "%Y-%m-%d %H:%M:%S").replace(tzinfo=tz)
        except ValueError:
            continue
        hour_key = dt.replace(minute=0, second=0, microsecond=0)
        wh_period[hour_key] = wh_period.get(hour_key, 0.0) + float(val)

    # Re-aggregate wh_days from the (now-hourly) wh_period.
    wh_days: dict[datetime, float] = {}
    for ts, val in wh_period.items():
        day_key = ts.replace(hour=0, minute=0, second=0, microsecond=0)
        wh_days[day_key] = wh_days.get(day_key, 0.0) + val

    sorted_keys = sorted(wh_period.keys())
    first_ts = sorted_keys[0].isoformat() if sorted_keys else "–"
    last_ts = sorted_keys[-1].isoformat() if sorted_keys else "–"
    _LOGGER.debug(
        "[smart_pv_forecast] forecast.solar parsed: tz=%r  %d hourly keys  first=%s  last=%s",
        tz_str, len(sorted_keys), first_ts, last_ts,
    )

    return _ArrayEstimate(timezone=tz_str, wh_period=wh_period, wh_days=wh_days)


def _merge_array_estimates(arrays: list[_ArrayEstimate]) -> ForecastSolarEstimate:
    """Sum multiple per-array estimates into one combined estimate."""
    if not arrays:
        raise ValueError("No array estimates to merge")

    tz = arrays[0].timezone

    if len(arrays) == 1:
        ae = arrays[0]
        watts = {k: v for k, v in ae.wh_period.items()}
        return ForecastSolarEstimate(
            timezone=tz,
            watts=watts,
            wh_period=dict(ae.wh_period),
            wh_days=dict(ae.wh_days),
        )

    # Sum all arrays
    wh_period: dict[datetime, float] = {}
    wh_days: dict[datetime, float] = {}

    for ae in arrays:
        for k, v in ae.wh_period.items():
            wh_period[k] = wh_period.get(k, 0.0) + float(v)
        for k, v in ae.wh_days.items():
            wh_days[k] = wh_days.get(k, 0.0) + float(v)

    # watts = wh_period (numerically equal for 1-hour periods in W)
    watts = {k: v for k, v in wh_period.items()}

    return ForecastSolarEstimate(
        timezone=tz,
        watts=watts,
        wh_period=wh_period,
        wh_days=wh_days,
    )
