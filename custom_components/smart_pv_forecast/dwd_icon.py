"""DWD ICON (via open-meteo) client + PV estimate builder."""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol
from typing import Any
from zoneinfo import ZoneInfo

from aiohttp import ClientSession

from .models import AccountType, Estimate


_OPEN_METEO_URL = "https://api.open-meteo.com/v1/dwd-icon"


@dataclass(frozen=True, slots=True)
class DwdIconPayload:
    timezone: str
    hourly_time: list[str]
    shortwave_radiation: list[float]


class RadiationPayload(Protocol):
    timezone: str
    hourly_time: list[str]
    shortwave_radiation: list[float]


class DwdIconClient:
    def __init__(self, session: ClientSession) -> None:
        self._session = session

    async def fetch(self, *, latitude: float, longitude: float) -> DwdIconPayload:
        params = {
            "latitude": latitude,
            "longitude": longitude,
            "hourly": "shortwave_radiation",
            "timezone": "auto",
        }

        async with self._session.get(_OPEN_METEO_URL, params=params) as resp:
            resp.raise_for_status()
            data = await resp.json()

        hourly = data.get("hourly") or {}
        tz = data.get("timezone") or "UTC"
        times = hourly.get("time") or []
        swr = hourly.get("shortwave_radiation") or []

        if not isinstance(times, list) or not isinstance(swr, list) or len(times) != len(swr):
            raise ValueError("Unexpected open-meteo response format")

        return DwdIconPayload(
            timezone=str(tz),
            hourly_time=[str(x) for x in times],
            shortwave_radiation=[float(x) for x in swr],
        )


def _angle_in_range(angle: float, start: float, end: float) -> bool:
    """Check if angle is within [start,end] with wrap-around support."""
    angle = angle % 360

    # Preserve raw values to detect ranges like 0..360.
    start_raw = float(start)
    end_raw = float(end)

    # Special-case end=360: treat it as 360° (not 0°) to allow ranges like 270..360
    # without unintentionally wrapping to 0°.
    start = start_raw % 360
    end = 360.0 if end_raw == 360.0 else (end_raw % 360)

    # If start and end normalize to the same angle but the raw values differ,
    # the intent is a full-circle range (e.g., 0..360).
    if start == end and start_raw != end_raw:
        return True
    if start <= end:
        return start <= angle <= end
    return angle >= start or angle <= end


def _parse_pv_arrays(value: Any) -> list[dict[str, float]]:
    """Accept already-parsed list or a JSON string; return normalized PV arrays.

    Each item: {"peak": Wp, "azimuth": deg, "tilt": deg}
    """
    if value in (None, ""):
        return []

    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return []

    if not isinstance(value, list):
        return []

    normalized: list[dict[str, float]] = []
    for item in value:
        if not isinstance(item, dict):
            continue

        peak_raw = item.get("peak")
        az_raw = item.get("azimuth")
        tilt_raw = item.get("tilt")

        if peak_raw is None or az_raw is None or tilt_raw is None:
            continue

        try:
            peak = float(peak_raw)
            az = float(az_raw)
            tilt = float(tilt_raw)
        except (TypeError, ValueError):
            continue

        if peak <= 0:
            continue

        normalized.append(
            {
                "peak": peak,
                "azimuth": az % 360,
                "tilt": max(0.0, min(90.0, tilt)),
            }
        )

    return normalized


def _poa_factor(*, sun_az: float, sun_el: float, panel_az: float, panel_tilt: float) -> float:
    """Approximate plane-of-array factor from global horizontal irradiance.

    Uses a simple cosine projection model to weight power by orientation.
    The input radiation is treated as horizontal; we approximate the relative
    gain/loss vs. horizontal by dividing by sin(elevation).
    """
    sin_el = math.sin(math.radians(sun_el))
    if sin_el <= 0.0:
        return 0.0

    az = math.radians(sun_az)
    el = math.radians(sun_el)
    pa = math.radians(panel_az)
    tilt = math.radians(panel_tilt)

    # cos(incidence) between sun vector and panel normal
    cos_inc = math.cos(el) * math.sin(tilt) * math.cos(az - pa) + math.sin(el) * math.cos(tilt)
    if cos_inc <= 0.0:
        return 0.0

    # Avoid extreme amplification at very low sun elevation.
    denom = max(0.15, sin_el)
    factor = cos_inc / denom

    # Keep POA correction conservative for this integration because the upstream
    # radiation signal is already global and amplified factors can otherwise
    # produce unrealistic peaks in multi-array setups.
    return max(0.0, min(1.0, float(factor)))


def _sun_position(latitude: float, longitude: float, when: datetime) -> tuple[float, float]:
    """Return (azimuth_deg, elevation_deg) for the given datetime."""
    # astral is bundled with Home Assistant.
    from astral import Observer  # type: ignore
    from astral.sun import azimuth, elevation  # type: ignore

    if when.tzinfo is None:
        when = when.astimezone()

    observer = Observer(latitude=latitude, longitude=longitude)
    return float(azimuth(observer, when)), float(elevation(observer, when))


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


def _iter_payload_slots(
    payload: RadiationPayload,
    *,
    tz: ZoneInfo,
) -> list[tuple[datetime, datetime, float]]:
    """Return normalized (hour_key, midpoint, radiation) payload tuples."""
    slots: list[tuple[datetime, datetime, float]] = []
    for ts_str, swr in zip(payload.hourly_time, payload.shortwave_radiation, strict=True):
        parsed_ts = datetime.fromisoformat(ts_str)
        if parsed_ts.tzinfo is None:
            ts = parsed_ts.replace(tzinfo=tz)
        else:
            ts = parsed_ts.astimezone(tz)

        hour_key = ts.replace(minute=0, second=0, microsecond=0)
        midpoint = hour_key + timedelta(minutes=30)
        slots.append((hour_key, midpoint, max(0.0, float(swr))))
    return slots


def build_array_watts(
    payload: RadiationPayload,
    *,
    latitude: float,
    longitude: float,
    pv_arrays: Any,
    damping_morning: float,
    damping_evening: float,
    inverter_size_watt: float | None,
    horizon_deg: float,
    array_factor_fn: Callable[[int, datetime], float] | None = None,
) -> list[dict[datetime, float]]:
    """Return per-array hourly watts for the given payload."""
    tz = ZoneInfo(payload.timezone)
    arrays = _parse_pv_arrays(pv_arrays)
    if not arrays:
        return []

    array_watts: list[dict[datetime, float]] = [dict() for _ in arrays]
    slots = _iter_payload_slots(payload, tz=tz)

    for hour_key, midpoint, ghi in slots:
        sun_az, sun_el = _sun_position(latitude, longitude, midpoint)
        local_hour = midpoint.hour
        damping_factor = max(
            0.0,
            1.0 - float(damping_morning if local_hour < 12 else damping_evening),
        )

        for idx, arr in enumerate(arrays):
            if sun_el < float(horizon_deg):
                arr_power = 0.0
            else:
                poa_factor = _poa_factor(
                    sun_az=sun_az,
                    sun_el=sun_el,
                    panel_az=arr["azimuth"],
                    panel_tilt=arr["tilt"],
                )
                arr_power = (ghi / 1000.0) * float(arr["peak"]) * poa_factor
                arr_power *= damping_factor

                if array_factor_fn is not None:
                    try:
                        factor = float(array_factor_fn(idx, hour_key))
                    except (TypeError, ValueError):
                        factor = 1.0
                    arr_power *= max(0.2, min(1.5, factor))

            array_watts[idx][hour_key] = float(arr_power)

    total_peak_watt = sum(float(arr["peak"]) for arr in arrays)
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

    return array_watts


def build_estimate(
    payload: RadiationPayload,
    *,
    latitude: float,
    longitude: float,
    pv_arrays: Any,
    damping_morning: float,
    damping_evening: float,
    inverter_size_watt: float | None,
    horizon_deg: float,
    array_factor_fn: Callable[[int, datetime], float] | None = None,
) -> Estimate:
    """Build a PV estimate from a radiation payload.
    """
    tz = ZoneInfo(payload.timezone)
    arrays = _parse_pv_arrays(pv_arrays)

    if arrays:
        array_watts = build_array_watts(
            payload,
            latitude=latitude,
            longitude=longitude,
            pv_arrays=arrays,
            damping_morning=damping_morning,
            damping_evening=damping_evening,
            inverter_size_watt=inverter_size_watt,
            horizon_deg=horizon_deg,
            array_factor_fn=array_factor_fn,
        )
        watts: dict[datetime, float] = {}
        for idx in range(len(array_watts)):
            for ts, val in array_watts[idx].items():
                watts[ts] = float(watts.get(ts, 0.0) + float(val))
    else:
        watts = {
            hour_key: 0.0 for hour_key, _, _ in _iter_payload_slots(payload, tz=tz)
        }

    # Hourly buckets are one-hour energy values derived from scaled power.
    wh_period: dict[datetime, float] = {
        ts: float(power_w) for ts, power_w in watts.items()
    }

    # Daily aggregation: keep same shape as old diagnostics
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
