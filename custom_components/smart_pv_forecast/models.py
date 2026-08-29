"""Internal models for the DWD solar forecast integration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import Enum
from typing import Mapping


class AccountType(str, Enum):
    """Account type placeholder (kept for compatibility with sensors/diagnostics)."""

    DWD_ICON = "dwd-icon"


@dataclass(frozen=True, slots=True)
class Estimate:
    """Container that mimics the Forecast.Solar Estimate API used by this integration."""

    timezone: str
    watts: Mapping[datetime, float]
    wh_period: Mapping[datetime, float]
    wh_days: Mapping[datetime, float]

    api_rate_limit: int | None = None
    account_type: AccountType = AccountType.DWD_ICON

    @staticmethod
    def now() -> datetime:
        """Return 'now'.

        Kept as a staticmethod to match the API used in sensor descriptions.
        """
        return datetime.now().astimezone()

    def _local_now(self, now: datetime | None = None) -> datetime:
        if now is None:
            now = self.now()
        if now.tzinfo is None:
            return now.astimezone()
        return now

    def _closest_hour_key(self, when: datetime) -> datetime | None:
        if not self.watts:
            return None
        # Normalize to hour key
        if when.tzinfo is None:
            when = when.astimezone()
        when_hour = when.replace(minute=0, second=0, microsecond=0)
        if when_hour in self.watts:
            return when_hour
        # fallback: nearest key
        return min(self.watts.keys(), key=lambda dt: abs((dt - when_hour).total_seconds()))

    @property
    def power_production_now(self) -> float | None:
        key = self._closest_hour_key(self._local_now())
        return None if key is None else float(self.watts.get(key, 0.0))

    def power_production_at_time(self, when: datetime) -> float | None:
        key = self._closest_hour_key(when)
        return None if key is None else float(self.watts.get(key, 0.0))

    def sum_energy_production(self, hours: int) -> float:
        if hours <= 0:
            return 0.0
        now = self._local_now()
        start = now.replace(minute=0, second=0, microsecond=0)
        total = 0.0
        for i in range(hours):
            ts = start + timedelta(hours=i)
            total += float(self.wh_period.get(ts, 0.0))
        return total

    @property
    def energy_current_hour(self) -> float:
        now = self._local_now()
        key = now.replace(minute=0, second=0, microsecond=0)
        return float(self.wh_period.get(key, 0.0))

    @property
    def energy_production_today(self) -> float:
        today = self._local_now().date()
        return self._sum_day(today)

    @property
    def energy_production_today_remaining(self) -> float:
        now = self._local_now()
        today = now.date()
        start = now.replace(minute=0, second=0, microsecond=0)
        end = datetime.combine(today, datetime.min.time(), tzinfo=now.tzinfo) + timedelta(
            days=1
        )
        total = 0.0
        ts = start
        while ts < end:
            total += float(self.wh_period.get(ts, 0.0))
            ts += timedelta(hours=1)
        return total

    @property
    def energy_production_tomorrow(self) -> float:
        tomorrow = self._local_now().date() + timedelta(days=1)
        return self._sum_day(tomorrow)

    @property
    def power_highest_peak_time_today(self) -> datetime | None:
        today = self._local_now().date()
        return self._peak_time_for_day(today)

    @property
    def power_highest_peak_time_tomorrow(self) -> datetime | None:
        tomorrow = self._local_now().date() + timedelta(days=1)
        return self._peak_time_for_day(tomorrow)

    def _sum_day(self, day: date) -> float:
        if not self.wh_period:
            return 0.0
        total = 0.0
        for ts, val in self.wh_period.items():
            if ts.date() == day:
                total += float(val)
        return total

    def energy_production_in_days(self, offset_days: int) -> float:
        """Return energy production for day 'today + offset_days'."""
        base = self._local_now().date()
        return self._sum_day(base + timedelta(days=offset_days))

    def _peak_time_for_day(self, day: date) -> datetime | None:
        candidates = [(ts, float(w)) for ts, w in self.watts.items() if ts.date() == day]
        if not candidates:
            return None
        return max(candidates, key=lambda x: x[1])[0]
