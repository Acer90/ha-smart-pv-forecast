"""Open-Meteo global forecast client using tilted irradiance per PV array."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Final
from zoneinfo import ZoneInfo

from aiohttp import ClientSession

from .models import AccountType, Estimate

_OPEN_METEO_FORECAST_URL: Final[str] = "https://api.open-meteo.com/v1/forecast"


@dataclass(frozen=True, slots=True)
class OpenMeteoTiltedPayload:
    timezone: str
    hourly_time: list[str]
    array_global_tilted_irradiance: list[list[float | None]]


def _ha_azimuth_to_open_meteo(ha_azimuth: float) -> float:
    """Convert HA azimuth (0..360, 180=south) to Open-Meteo convention."""
    az = (float(ha_azimuth) % 360.0) - 180.0
    if az < -180.0:
        az += 360.0
    if az > 180.0:
        az -= 360.0
    return az


def _sun_elevation(latitude: float, longitude: float, when: datetime) -> float:
    """Return solar elevation in degrees for horizon filtering."""
    from astral import Observer  # type: ignore
    from astral.sun import elevation  # type: ignore

    observer = Observer(latitude=latitude, longitude=longitude)
    return float(elevation(observer, when))


def _daily_scale_factors_for_limit(
    watts: dict[datetime, float], *, limit_watt: float
) -> dict[tuple[int, int, int], float]:
    """Return per-day scaling factors so daily peak does not exceed limit."""
    if limit_watt <= 0 or not watts:
        return {}

    day_peak: dict[tuple[int, int, int], float] = {}
    for ts, val in watts.items():
        day_key = (ts.year, ts.month, ts.day)
        day_peak[day_key] = max(day_peak.get(day_key, 0.0), float(val))

    day_factor: dict[tuple[int, int, int], float] = {}
    for day_key, peak in day_peak.items():
        if peak <= 0:
            day_factor[day_key] = 1.0
        elif peak > limit_watt:
            day_factor[day_key] = float(limit_watt) / float(peak)
        else:
            day_factor[day_key] = 1.0
    return day_factor


def build_array_watts_from_tilted_payload(
    payload: OpenMeteoTiltedPayload,
    *,
    latitude: float,
    longitude: float,
    pv_arrays: list[dict[str, Any]],
    damping_morning: float,
    damping_evening: float,
    inverter_size_watt: float | None,
    horizon_deg: float,
    shading_factor_fn: Callable[[int, datetime], float] | None = None,
) -> list[dict[datetime, float]]:
    """Build per-array hourly watts from Open-Meteo global tilted irradiance."""
    if not pv_arrays:
        return []

    tz = ZoneInfo(payload.timezone)
    array_watts: list[dict[datetime, float]] = [dict() for _ in pv_arrays]

    hourly_slots: list[datetime] = []
    for ts_str in payload.hourly_time:
        parsed_ts = datetime.fromisoformat(ts_str)
        if parsed_ts.tzinfo is None:
            ts = parsed_ts.replace(tzinfo=tz)
        else:
            ts = parsed_ts.astimezone(tz)
        hourly_slots.append(ts.replace(minute=0, second=0, microsecond=0))

    for idx, arr in enumerate(pv_arrays):
        irradiance_series = (
            payload.array_global_tilted_irradiance[idx]
            if idx < len(payload.array_global_tilted_irradiance)
            else []
        )

        for ts, gti in zip(hourly_slots, irradiance_series, strict=False):
            if gti is None:
                continue
            if _sun_elevation(
                arr.get("latitude", latitude), arr.get("longitude", longitude),
                ts + timedelta(minutes=30),
            ) < float(horizon_deg):
                arr_power = 0.0
            else:
                damping = max(
                    0.0,
                    1.0 - float(damping_morning if ts.hour < 12 else damping_evening),
                )
                arr_power = (max(0.0, float(gti)) / 1000.0) * float(arr["peak"]) * damping

            array_watts[idx][ts] = float(arr_power)

    total_peak_watt = sum(float(arr["peak"]) for arr in pv_arrays)
    effective_peak_limit: float | None = None
    if total_peak_watt > 0:
        effective_peak_limit = total_peak_watt
    if inverter_size_watt is not None and inverter_size_watt > 0:
        inverter_limit = float(inverter_size_watt)
        if effective_peak_limit is None:
            effective_peak_limit = inverter_limit
        else:
            effective_peak_limit = min(effective_peak_limit, inverter_limit)

    if effective_peak_limit is not None:
        total_watts: dict[datetime, float] = {}
        for idx in range(len(array_watts)):
            for ts, val in array_watts[idx].items():
                total_watts[ts] = float(total_watts.get(ts, 0.0) + float(val))

        day_factors = _daily_scale_factors_for_limit(total_watts, limit_watt=effective_peak_limit)
        if day_factors:
            for idx in range(len(array_watts)):
                scaled: dict[datetime, float] = {}
                for ts, val in array_watts[idx].items():
                    day_key = (ts.year, ts.month, ts.day)
                    scaled[ts] = float(val) * day_factors.get(day_key, 1.0)
                array_watts[idx] = scaled

    if shading_factor_fn is not None:
        array_watts = [
            {ts: value * shading_factor_fn(index, ts) for ts, value in values.items()}
            for index, values in enumerate(array_watts)
        ]
    return array_watts


def build_estimate_from_tilted_payload(
    payload: OpenMeteoTiltedPayload,
    *,
    latitude: float,
    longitude: float,
    pv_arrays: list[dict[str, Any]],
    damping_morning: float,
    damping_evening: float,
    inverter_size_watt: float | None,
    horizon_deg: float,
    shading_factor_fn: Callable[[int, datetime], float] | None = None,
) -> Estimate:
    """Build Estimate from Open-Meteo tilted irradiance payload."""
    array_watts = build_array_watts_from_tilted_payload(
        payload,
        latitude=latitude,
        longitude=longitude,
        pv_arrays=pv_arrays,
        damping_morning=damping_morning,
        damping_evening=damping_evening,
        inverter_size_watt=inverter_size_watt,
        horizon_deg=horizon_deg,
        shading_factor_fn=shading_factor_fn,
    )

    watts: dict[datetime, float] = {}
    for idx in range(len(array_watts)):
        for ts, val in array_watts[idx].items():
            watts[ts] = float(watts.get(ts, 0.0) + float(val))

    wh_period = {ts: float(val) for ts, val in watts.items()}
    wh_days: dict[datetime, float] = {}
    for ts, val in wh_period.items():
        day_key = datetime(ts.year, ts.month, ts.day, tzinfo=ts.tzinfo)
        wh_days[day_key] = float(wh_days.get(day_key, 0.0) + float(val))

    return Estimate(
        timezone=payload.timezone,
        watts=watts,
        wh_period=wh_period,
        wh_days=wh_days,
        api_rate_limit=None,
        account_type=AccountType.DWD_ICON,
    )


class OpenMeteoClient:
    """Fetches hourly global tilted irradiance from Open-Meteo global forecast."""

    def __init__(self, session: ClientSession) -> None:
        self._session = session

    async def fetch_radiation(
        self,
        *,
        latitude: float,
        longitude: float,
        pv_arrays: list[dict[str, Any]],
        forecast_days: int = 7,
    ) -> OpenMeteoTiltedPayload:
        forecast_days = max(1, min(16, int(forecast_days)))
        if not pv_arrays:
            raise ValueError("No PV arrays configured for Open-Meteo")

        timezone: str | None = None
        reference_times: list[str] | None = None
        irradiance_series: list[list[float | None]] = []

        for arr in pv_arrays:
            params = {
                "latitude": arr.get("latitude", latitude),
                "longitude": arr.get("longitude", longitude),
                "hourly": "global_tilted_irradiance",
                "tilt": float(arr["tilt"]),
                "azimuth": _ha_azimuth_to_open_meteo(float(arr["azimuth"])),
                "timezone": "auto",
                "forecast_days": forecast_days,
            }

            async with self._session.get(_OPEN_METEO_FORECAST_URL, params=params) as resp:
                resp.raise_for_status()
                data = await resp.json()

            hourly = data.get("hourly") or {}
            tz = str(data.get("timezone") or "UTC")
            times = hourly.get("time") or []
            gti = hourly.get("global_tilted_irradiance") or []

            if (
                not isinstance(times, list)
                or not isinstance(gti, list)
                or len(times) != len(gti)
            ):
                raise ValueError("Unexpected Open-Meteo response format")

            if timezone is None:
                timezone = tz
            if reference_times is None:
                reference_times = [str(t) for t in times]
                irradiance_series.append(
                    [float(v) if v is not None else None for v in gti]
                )
                continue

            by_time = {
                str(t): float(v) if v is not None else None
                for t, v in zip(times, gti, strict=True)
            }
            aligned = [by_time.get(ts) for ts in reference_times]
            irradiance_series.append(aligned)

        return OpenMeteoTiltedPayload(
            timezone=timezone or "UTC",
            hourly_time=reference_times or [],
            array_global_tilted_irradiance=irradiance_series,
        )
