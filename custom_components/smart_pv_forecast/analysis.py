"""Shared per-surface combination and transparent local-day comparison."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone as utc_timezone
from typing import Any
from zoneinfo import ZoneInfo

from .models import AccountType, Estimate


def combine_arrays(sources: list[tuple[list[dict[datetime, float]], float]],
                   count: int) -> list[dict[datetime, float]]:
    result: list[dict[datetime, float]] = []
    for index in range(count):
        maps = [(arrays[index], weight) for arrays, weight in sources]
        keys = {ts for values, _ in maps for ts in values}
        combined: dict[datetime, float] = {}
        for ts in sorted(keys, key=lambda value: value.timestamp()):
            active = [(values[ts], weight) for values, weight in maps if ts in values]
            total = sum(weight for _, weight in active)
            combined[ts] = (sum(value * weight for value, weight in active) / total
                            if total > 0 else sum(value for value, _ in active) / len(active))
        result.append(combined)
    return result


def group_arrays(values: list[dict[datetime, float]], parents: list[int],
                 count: int) -> list[dict[datetime, float]]:
    grouped: list[dict[datetime, float]] = [{} for _ in range(count)]
    members: list[list[dict[datetime, float]]] = [[] for _ in range(count)]
    for surface, parent in zip(values, parents, strict=True):
        members[parent].append(surface)
    # A total is known only where every contributing surface has a value.
    for parent, surfaces in enumerate(members):
        keys = set.intersection(*(set(surface) for surface in surfaces)) if surfaces else set()
        grouped[parent] = {
            ts: sum(surface[ts] for surface in surfaces)
            for ts in sorted(keys, key=lambda value: value.timestamp())
        }
    return grouped


def apply_inverter_limits(values: list[dict[datetime, float]], surfaces: list[dict[str, Any]],
                          inverters: list[dict[str, Any]]) -> list[dict[datetime, float]]:
    """Use the existing conservative daily scaling, independently per inverter."""
    result = [dict(v) for v in values]
    for inverter in inverters:
        indices = [i for i, surface in enumerate(surfaces) if surface["inverter_id"] == inverter["id"]]
        total: dict[datetime, float] = {}
        for index in indices:
            for ts, value in values[index].items():
                total[ts] = total.get(ts, 0) + value
        peaks: dict[date, float] = {}
        for ts, value in total.items():
            peaks[ts.date()] = max(peaks.get(ts.date(), 0), value)
        for index in indices:
            result[index] = {
                ts: value * min(1, inverter["limit_w"] / peaks[ts.date()]) if peaks[ts.date()] > 0 else value
                for ts, value in values[index].items()
            }
    return result


def estimate_from_arrays(values: list[dict[datetime, float]], timezone: str) -> Estimate:
    total = group_arrays(values, [0] * len(values), 1)[0]
    days: dict[datetime, float] = {}
    for ts, value in total.items():
        day = ts.replace(hour=0, minute=0, second=0, microsecond=0)
        days[day] = days.get(day, 0) + value
    return Estimate(timezone=timezone, watts=total, wh_period=dict(total),
                    wh_days=days, account_type=AccountType.DWD_ICON)


def _day_report(name: str, baseline: dict[datetime, float], shaded: dict[datetime, float],
                tz: ZoneInfo, day: date) -> dict[str, Any]:
    buckets: list[list[tuple[float, float]]] = [[] for _ in range(24)]
    for ts, value in baseline.items():
        local = ts.astimezone(tz)
        if local.date() == day:
            buckets[local.hour].append((value, shaded[ts]))
    hours: list[dict[str, Any]] = []
    for hour, entries in enumerate(buckets):
        base = sum(b for b, _ in entries) / len(entries) if entries else None
        actual = sum(s for _, s in entries) / len(entries) if entries else None
        hours.append({
            "hour": hour, "baseline_w": base, "shaded_w": actual,
            "baseline_wh": sum(b for b, _ in entries) if entries else None,
            "shaded_wh": sum(s for _, s in entries) if entries else None,
            "reduction_percent": 100 * (1 - actual / base) if base and actual is not None else 0 if base == 0 else None,
        })
    base_wh = sum(h["baseline_wh"] or 0 for h in hours)
    actual_wh = sum(h["shaded_wh"] or 0 for h in hours)
    return {
        "name": name, "hours": hours,
        "baseline_wh": base_wh, "shaded_wh": actual_wh,
        "reduction_percent": 100 * (1 - actual_wh / base_wh) if base_wh else 0,
        "covered_hours": sum(bool(b) for b in buckets),
    }


def daily_analysis(arrays: list[dict[str, Any]], baseline: list[dict[datetime, float]],
                   shaded: list[dict[datetime, float]], timezone: str,
                   when: datetime, revision: str, sources: list[str],
                   forecast_day_count: int = 7) -> dict[str, Any]:
    tz = ZoneInfo(timezone)
    day = when.astimezone(tz).date()
    result = [_day_report(array["name"], baseline[index], shaded[index], tz, day)
              for index, array in enumerate(arrays)]
    # A whole-site value requires every array at the same real instant, including DST folds.
    maps = [*baseline, *shaded]
    shared = set.intersection(*(set(values) for values in maps)) if maps else set()
    total_baseline = {ts: sum(values[ts] for values in baseline) for ts in shared}
    total_shaded = {ts: sum(values[ts] for values in shaded) for ts in shared}
    total = _day_report("Total", total_baseline, total_shaded, tz, day)
    forecast_days = []
    for offset in range(forecast_day_count):
        forecast_day = day + timedelta(days=offset)
        slots = [ts for ts in shared if ts.astimezone(tz).date() == forecast_day]
        start = datetime.combine(forecast_day, datetime.min.time(), tzinfo=tz)
        end = datetime.combine(forecast_day + timedelta(days=1), datetime.min.time(), tzinfo=tz)
        expected = (end.astimezone(utc_timezone.utc) - start.astimezone(utc_timezone.utc)).total_seconds() / 3600
        forecast_days.append({
            "date": forecast_day.isoformat(),
            "baseline_wh": sum(total_baseline[ts] for ts in slots) if slots else None,
            "shaded_wh": sum(total_shaded[ts] for ts in slots) if slots else None,
            "covered_hours": len(slots), "expected_hours": expected,
        })
    return {"date": day.isoformat(), "timezone": timezone, "revision": revision,
            "sources": sources, "arrays": result, "total": total, "forecast_days": forecast_days,
            "generated_at": when.isoformat()}
