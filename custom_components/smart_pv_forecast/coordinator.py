"""DataUpdateCoordinator for the Smart PV Forecast integration."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from typing import TypeAlias

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_LATITUDE, CONF_LONGITUDE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util.dt import now as dt_now

from .const import (
    CONF_BATTERY_CHARGE_LIMIT,
    CONF_BATTERY_CHARGE_POWER_ENTITY,
    CONF_BATTERY_SOC_ENTITY,
    CONF_BATTERY_SOC_THRESHOLD,
    CONF_DAMPING_EVENING,
    CONF_DAMPING_MORNING,
    CONF_ENABLE_DWD_SOURCE,
    CONF_ENABLE_FORECAST_SOLAR_SOURCE,
    CONF_ENABLE_OPEN_METEO_SOURCE,
    CONF_ENABLE_SELF_OPTIMIZATION,
    CONF_FORECAST_SOLAR_API_KEY,
    CONF_HORIZON,
    CONF_INVERTER_SIZE,
    CONF_MOSMIX_STATION,
    CONF_OPEN_METEO_FORECAST_DAYS,
    CONF_PV_ARRAYS,
    CONF_REAL_POWER_ENTITIES,
    CONF_SOURCE_WEIGHT_DWD,
    CONF_SOURCE_WEIGHT_FORECAST_SOLAR,
    CONF_SOURCE_WEIGHT_OPEN_METEO,
    DOMAIN,
    LOGGER,
)

from .dwd_icon import _parse_pv_arrays, build_array_watts, build_estimate
from .dwd_mosmix import DwdMosmixClient
from .forecast_solar_source import ForecastSolarClient
from .models import Estimate, AccountType
from .open_meteo_source import (
    OpenMeteoClient,
    build_array_watts_from_tilted_payload,
    build_estimate_from_tilted_payload,
)

ForecastSolarConfigEntry: TypeAlias = ConfigEntry["ForecastSolarDataUpdateCoordinator"]

_ADAPTIVE_MODEL_VERSION = 1
_ADAPTIVE_SEASON_BUCKETS = 24


class ForecastSolarDataUpdateCoordinator(DataUpdateCoordinator[Estimate]):
    """The Smart PV Forecast Data Update Coordinator."""

    config_entry: ForecastSolarConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ForecastSolarConfigEntry) -> None:
        """Initialize the coordinator."""

        session = async_get_clientsession(hass)
        self._dwd_client = DwdMosmixClient(session)
        self._open_meteo_client = OpenMeteoClient(session)
        self._forecast_solar_client = ForecastSolarClient(
            session,
            api_key=str(entry.options.get(CONF_FORECAST_SOLAR_API_KEY, "")),
        )

        update_interval = timedelta(hours=1)

        super().__init__(
            hass,
            LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=update_interval,
        )

        # Fingerprint of options used to compute the current cached estimate.
        self._options_fingerprint: str | None = None

        # Source-level payload cache: (payload, fetched_at) per source name.
        # TTL: DWD=6h (MOSMIX-L only updates 4x/day), OM=2h, FS=2h.
        # This prevents each hourly coordinator tick from fetching fresh API data
        # every time, which would cause the forecast to drift slightly each hour and
        # produce many "spaghetti" lines in history-based charts.
        self._payload_cache: dict[str, tuple[Any, datetime]] = {}
        _DWD_TTL_HOURS = 6
        _OM_TTL_HOURS = 2
        _FS_TTL_HOURS = 2
        self._source_ttl: dict[str, int] = {
            "dwd": _DWD_TTL_HOURS,
            "om": _OM_TTL_HOURS,
            "fs": _FS_TTL_HOURS,
        }

        # Energy forecast snapshot for the HA Energy Dashboard.
        # Updated only when fresh source data is fetched (not every hourly tick).
        # Persisted to disk so HA restarts don't produce new forecast lines.
        self._energy_snapshot: dict[str, float] = {}
        self._energy_snapshot_version: int = -1
        self._source_data_version: int = 0  # incremented on fresh API fetches
        self._energy_snapshot_path: Path = Path(
            hass.config.path(f"{DOMAIN}_{entry.entry_id}_energy_snapshot.json")
        )

        self._adaptive_store: Store[dict[str, Any]] = Store(
            hass,
            _ADAPTIVE_MODEL_VERSION,
            f"{DOMAIN}_{entry.entry_id}_adaptive_model",
        )
        self._adaptive_model: dict[str, Any] = {
            "version": _ADAPTIVE_MODEL_VERSION,
            "arrays": [],
        }
        self._adaptive_loaded: bool = False
        self._adaptive_last_learn_slot: str | None = None
        self._adaptive_status: str = "idle"
        self._adaptive_reason: str = "not_initialized"
        self._adaptive_snapshot: dict[str, Any] = {
            "enabled": False,
            "average_factor": 1.0,
            "average_reference_1000": 1000.0,
            "samples_total": 0,
            "arrays": [],
            "status": self._adaptive_status,
            "reason": self._adaptive_reason,
        }

    @staticmethod
    def _safe_float(value: Any) -> float | None:
        """Return float(value) or None for invalid input."""
        try:
            if value in (None, "", "unknown", "unavailable", "none"):
                return None
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _parse_entity_list(value: Any) -> list[str]:
        """Parse a JSON list of entity IDs from options."""
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        if not isinstance(value, str) or not value.strip():
            return []
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        if not isinstance(parsed, list):
            return []
        return [str(item).strip() for item in parsed if str(item).strip()]

    @staticmethod
    def _season_bucket(ts: datetime) -> int:
        """Map day-of-year into a coarse seasonal bucket."""
        day = max(1, int(ts.timetuple().tm_yday))
        return min(
            _ADAPTIVE_SEASON_BUCKETS - 1,
            int(((day - 1) * _ADAPTIVE_SEASON_BUCKETS) / 366),
        )

    @classmethod
    def _bin_key(cls, ts: datetime) -> str:
        """Return hour+season key for adaptive factors."""
        return f"s{cls._season_bucket(ts)}_h{ts.hour:02d}"

    def _set_adaptive_status(self, *, status: str, reason: str) -> None:
        """Update runtime status fields for diagnostics and sensors."""
        self._adaptive_status = status
        self._adaptive_reason = reason

    async def _async_ensure_adaptive_model_loaded(self) -> None:
        """Load persisted adaptive model once."""
        if self._adaptive_loaded:
            return

        stored = await self._adaptive_store.async_load()
        if isinstance(stored, dict):
            arrays = stored.get("arrays")
            if isinstance(arrays, list):
                self._adaptive_model = {
                    "version": _ADAPTIVE_MODEL_VERSION,
                    "arrays": arrays,
                }
            else:
                self._adaptive_model = {
                    "version": _ADAPTIVE_MODEL_VERSION,
                    "arrays": [],
                }
        else:
            self._adaptive_model = {
                "version": _ADAPTIVE_MODEL_VERSION,
                "arrays": [],
            }

        self._adaptive_loaded = True

    def _ensure_adaptive_array_count(self, count: int) -> None:
        """Grow adaptive model arrays list to match configured PV arrays."""
        arrays = self._adaptive_model.setdefault("arrays", [])
        if not isinstance(arrays, list):
            arrays = []
            self._adaptive_model["arrays"] = arrays

        while len(arrays) < count:
            arrays.append({"bins": {}})

    def _adaptive_samples_total(self) -> int:
        """Return total learned sample count across all arrays and bins."""
        total = 0
        arrays = self._adaptive_model.get("arrays", [])
        if not isinstance(arrays, list):
            return 0
        for array_data in arrays:
            if not isinstance(array_data, dict):
                continue
            bins = array_data.get("bins", {})
            if not isinstance(bins, dict):
                continue
            for bucket in bins.values():
                if not isinstance(bucket, dict):
                    continue
                try:
                    total += int(bucket.get("count", 0))
                except (TypeError, ValueError):
                    continue
        return total

    def _get_adaptive_factor(self, array_idx: int, ts: datetime) -> float:
        """Return learned factor for array/time slot (or 1.0 if unavailable)."""
        arrays = self._adaptive_model.get("arrays", [])
        if not isinstance(arrays, list) or array_idx < 0 or array_idx >= len(arrays):
            return 1.0

        array_data = arrays[array_idx]
        if not isinstance(array_data, dict):
            return 1.0

        bins = array_data.get("bins", {})
        if not isinstance(bins, dict):
            return 1.0

        bucket = bins.get(self._bin_key(ts), {})
        if not isinstance(bucket, dict):
            return 1.0

        try:
            factor = float(bucket.get("factor", 1.0))
        except (TypeError, ValueError):
            return 1.0
        return max(0.5, min(1.3, factor))

    def _get_adaptive_sample_count(self, array_idx: int, ts: datetime) -> int:
        """Return learned sample count for array/time slot."""
        arrays = self._adaptive_model.get("arrays", [])
        if not isinstance(arrays, list) or array_idx < 0 or array_idx >= len(arrays):
            return 0

        array_data = arrays[array_idx]
        if not isinstance(array_data, dict):
            return 0

        bins = array_data.get("bins", {})
        if not isinstance(bins, dict):
            return 0

        bucket = bins.get(self._bin_key(ts), {})
        if not isinstance(bucket, dict):
            return 0

        try:
            return max(0, int(bucket.get("count", 0)))
        except (TypeError, ValueError):
            return 0

    def _refresh_adaptive_snapshot(self, *, enabled: bool, local_now: datetime, array_count: int) -> None:
        """Build snapshot values used by adaptive sensors and diagnostics."""
        arrays_snapshot: list[dict[str, Any]] = []
        average_factor = 1.0

        if enabled and array_count > 0:
            factors: list[float] = []
            for idx in range(array_count):
                factor = self._get_adaptive_factor(idx, local_now)
                samples = self._get_adaptive_sample_count(idx, local_now)
                arrays_snapshot.append(
                    {
                        "index": idx,
                        "factor": round(factor, 4),
                        "reference_1000": round(factor * 1000.0, 1),
                        "samples": samples,
                    }
                )
                factors.append(factor)

            if factors:
                average_factor = sum(factors) / len(factors)

        self._adaptive_snapshot = {
            "enabled": enabled,
            "average_factor": round(average_factor, 4),
            "average_reference_1000": round(average_factor * 1000.0, 1),
            "samples_total": self._adaptive_samples_total(),
            "arrays": arrays_snapshot,
            "status": self._adaptive_status,
            "reason": self._adaptive_reason,
        }

    @staticmethod
    def _slot_value_from_map(values: dict[datetime, float], slot: datetime) -> float | None:
        """Get exact or nearest value for the requested hour slot."""
        if slot in values:
            return float(values[slot])

        nearest: float | None = None
        nearest_delta = 10**9
        for ts, val in values.items():
            delta = abs(int((ts - slot).total_seconds()))
            if delta < nearest_delta:
                nearest_delta = delta
                nearest = float(val)
        if nearest_delta <= 3600:
            return nearest
        return None

    async def _async_run_adaptive_learning(
        self,
        *,
        local_now: datetime,
        base_array_watts: list[dict[datetime, float]],
        pv_array_count: int,
        real_power_entities: list[str],
        battery_soc_entity: str,
        battery_charge_power_entity: str,
        battery_charge_limit: float,
        battery_soc_threshold: float,
    ) -> None:
        """Update adaptive model using real per-array power measurements."""
        await self._async_ensure_adaptive_model_loaded()

        if pv_array_count <= 0:
            self._set_adaptive_status(status="skipped", reason="no_arrays")
            return

        if len(real_power_entities) != pv_array_count:
            self._set_adaptive_status(status="skipped", reason="entity_count_mismatch")
            return

        hour_slot = local_now.replace(minute=0, second=0, microsecond=0)
        slot_key = hour_slot.isoformat()
        if self._adaptive_last_learn_slot == slot_key:
            self._set_adaptive_status(status="skipped", reason="already_learned_this_hour")
            return

        soc_value: float | None = None
        if battery_soc_entity:
            soc_state = self.hass.states.get(battery_soc_entity)
            soc_value = self._safe_float(soc_state.state if soc_state else None)
            if soc_value is not None and soc_value >= battery_soc_threshold:
                self._set_adaptive_status(status="filtered", reason="battery_soc_high")
                self._adaptive_last_learn_slot = slot_key
                return

        if battery_charge_power_entity and battery_charge_limit > 0:
            charge_state = self.hass.states.get(battery_charge_power_entity)
            charge_power = self._safe_float(charge_state.state if charge_state else None)
            if charge_power is not None and charge_power >= (0.98 * battery_charge_limit):
                self._set_adaptive_status(status="filtered", reason="battery_charge_limit")
                self._adaptive_last_learn_slot = slot_key
                return

        if not base_array_watts:
            self._set_adaptive_status(status="skipped", reason="missing_base_data")
            return

        self._ensure_adaptive_array_count(pv_array_count)

        min_base_power_watt = 120.0
        learned_count = 0
        for idx, entity_id in enumerate(real_power_entities):
            if idx >= len(base_array_watts):
                break

            base_power = self._slot_value_from_map(base_array_watts[idx], hour_slot)
            if base_power is None or base_power < min_base_power_watt:
                continue

            state = self.hass.states.get(entity_id)
            measured_power = self._safe_float(state.state if state else None)
            if measured_power is None or measured_power <= 0.0:
                continue

            ratio = measured_power / base_power
            ratio = max(0.5, min(1.3, ratio))

            arrays = self._adaptive_model["arrays"]
            array_data = arrays[idx]
            bins = array_data.setdefault("bins", {})
            bucket_key = self._bin_key(hour_slot)
            bucket = bins.setdefault(bucket_key, {"factor": 1.0, "count": 0})

            old_factor = self._safe_float(bucket.get("factor")) or 1.0
            old_count = int(bucket.get("count", 0))
            alpha = 0.35 if old_count < 6 else 0.18
            new_factor = old_factor + alpha * (ratio - old_factor)
            bucket["factor"] = round(max(0.5, min(1.3, new_factor)), 6)
            bucket["count"] = min(20000, old_count + 1)
            learned_count += 1

        self._adaptive_last_learn_slot = slot_key
        if learned_count == 0:
            self._set_adaptive_status(status="skipped", reason="no_valid_samples")
            return

        self._set_adaptive_status(status="learned", reason=f"arrays={learned_count}")
        await self._adaptive_store.async_save(self._adaptive_model)

    def get_adaptive_snapshot(self) -> dict[str, Any]:
        """Return latest adaptive runtime snapshot for sensors and diagnostics."""
        return dict(self._adaptive_snapshot)

    def get_adaptive_array_metric(self, array_index: int, metric: str) -> Any:
        """Get a single per-array adaptive metric by index."""
        arrays = self._adaptive_snapshot.get("arrays", [])
        if not isinstance(arrays, list):
            return None
        if array_index < 0 or array_index >= len(arrays):
            return None
        data = arrays[array_index]
        if not isinstance(data, dict):
            return None
        return data.get(metric)

    @staticmethod
    def _aggregate_wh_days(wh_period: dict[datetime, float]) -> dict[datetime, float]:
        """Aggregate hourly energy into daily buckets."""
        wh_days: dict[datetime, float] = {}
        for ts, val in wh_period.items():
            day_key = datetime(ts.year, ts.month, ts.day, tzinfo=ts.tzinfo)
            wh_days[day_key] = float(wh_days.get(day_key, 0.0) + float(val))
        return wh_days

    def get_energy_wh_hours(self) -> dict[str, float]:
        """Return wh_hours for the HA Energy Dashboard.

        Returns the in-memory snapshot that is refreshed by _async_update_data
        after each successful API fetch. Never does file I/O.
        """
        return self._energy_snapshot

    async def _async_init_energy_snapshot(self) -> None:
        """Load persisted energy snapshot from disk (called once at first update)."""
        try:
            def _do_load() -> dict[str, float]:
                import json as _json
                from datetime import date as _date
                if not self._energy_snapshot_path.exists():
                    return {}
                data = _json.loads(self._energy_snapshot_path.read_text(encoding="utf-8"))
                if not isinstance(data, dict) or not data:
                    return {}
                today_str = _date.today().isoformat()
                if not any(k[:10] >= today_str for k in data):
                    LOGGER.debug("[smart_pv_forecast] Energy snapshot is stale – discarding")
                    return {}
                LOGGER.debug(
                    "[smart_pv_forecast] Loaded energy snapshot from disk (%d keys)", len(data)
                )
                return {k: float(v) for k, v in data.items()}

            self._energy_snapshot = await self.hass.async_add_executor_job(_do_load)
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("[smart_pv_forecast] Could not load energy snapshot: %s", exc)

    def _load_energy_snapshot(self) -> dict[str, float]:
        """Kept for compatibility – always returns empty dict (use async variant)."""
        return {}

    async def _async_save_energy_snapshot(self, snapshot: dict[str, float]) -> None:
        """Persist energy snapshot to disk (offloaded to executor)."""
        try:
            def _do_save() -> None:
                import json as _json
                self._energy_snapshot_path.write_text(
                    _json.dumps(snapshot, indent=None, separators=(",", ":")),
                    encoding="utf-8",
                )
                LOGGER.debug(
                    "[smart_pv_forecast] Saved energy snapshot to disk (%d keys)", len(snapshot)
                )
            await self.hass.async_add_executor_job(_do_save)
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("[smart_pv_forecast] Could not save energy snapshot: %s", exc)

    def _save_energy_snapshot(self, snapshot: dict[str, float]) -> None:
        """Kept for compatibility – no-op (use async variant)."""

    async def _async_update_energy_snapshot(self, estimate: "Estimate") -> None:
        """Rebuild and persist the energy snapshot from the latest estimate."""
        candidate: dict[str, float] = {
            ts.isoformat(): round(float(val), 1)
            for ts, val in estimate.wh_period.items()
        }
        changed = not self._energy_snapshot or any(
            abs(candidate.get(k, 0.0) - self._energy_snapshot.get(k, 0.0)) > 20.0
            for k in set(candidate) | set(self._energy_snapshot)
        )
        if changed:
            self._energy_snapshot = candidate
            self._energy_snapshot_version = self._source_data_version
            await self._async_save_energy_snapshot(candidate)

    @staticmethod
    def _combine_estimates(
        sources: list[tuple["Estimate | None", int]],
        fallback_timezone: str,
    ) -> "Estimate":
        """Combine N estimates using normalized weighted average per time slot.

        sources: list of (estimate, weight) tuples; weight 0-100.
        Weights are normalized across active (non-None) sources.
        If all weights are 0, active sources get equal weight.
        """
        _SOURCE_NAMES = ["DWD", "OpenMeteo", "ForecastSolar"]

        active = [(est, w) for est, w in sources if est is not None]
        active_names = [
            _SOURCE_NAMES[i] if i < len(_SOURCE_NAMES) else f"Source{i}"
            for i, (est, _) in enumerate(sources)
            if est is not None
        ]

        if not active:
            raise ValueError("No estimates to combine")

        # --- Diagnostic logging ---
        LOGGER.debug(
            "[smart_pv_forecast] _combine_estimates: %d active source(s): %s",
            len(active),
            active_names,
        )
        for name, (est, w) in zip(active_names, active):
            keys = sorted(est.watts.keys())  # type: ignore[union-attr]
            first_ts = keys[0].isoformat() if keys else "–"
            last_ts = keys[-1].isoformat() if keys else "–"
            LOGGER.debug(
                "[smart_pv_forecast]   %s (weight=%d): tz=%r  %d keys  first=%s  last=%s",
                name, w, getattr(est, "timezone", "?"), len(keys), first_ts, last_ts,
            )

        if len(active) == 1:
            LOGGER.debug("[smart_pv_forecast] Single source → returning directly")
            return active[0][0]  # type: ignore[return-value]

        # Check for timezone mismatches (UTC offset of first key)
        offsets = {}
        for name, (est, _) in zip(active_names, active):
            keys = list(est.watts.keys())  # type: ignore[union-attr]
            if keys:
                off = keys[0].utcoffset()
                offsets[name] = str(off)
        LOGGER.debug("[smart_pv_forecast]   UTC offsets of first key: %s", offsets)

        total_w = sum(w for _, w in active)
        if total_w == 0:
            equal = 1.0 / len(active)
            weights = [equal] * len(active)
        else:
            weights = [w / total_w for _, w in active]

        LOGGER.debug(
            "[smart_pv_forecast]   Normalized weights: %s",
            {n: f"{wn:.3f}" for n, wn in zip(active_names, weights)},
        )

        # Log overlap statistics (using pre-binned keys for diagnostics)
        all_keys_raw: set = set()
        per_source_keys: list[set] = []
        for est, _ in active:
            sk = set(est.watts.keys())  # type: ignore[union-attr]
            per_source_keys.append(sk)
            all_keys_raw |= sk

        if len(active) > 1:
            overlap = per_source_keys[0]
            for sk in per_source_keys[1:]:
                overlap = overlap & sk
            LOGGER.debug(
                "[smart_pv_forecast]   Total keys in union: %d  |  Keys in ALL sources (overlap): %d  |  "
                "Keys only in one source: %d",
                len(all_keys_raw),
                len(overlap),
                len(all_keys_raw) - len(overlap),
            )
            if len(overlap) == 0:
                LOGGER.warning(
                    "[smart_pv_forecast]   *** NO OVERLAP between sources – timezone mismatch! "
                    "Check that all sources use the same UTC offset. ***"
                )

        # ------------------------------------------------------------------
        # Bin each source to exact hour boundaries before combining.
        # watts (W, power) → average sub-hourly values.
        # wh_period (Wh, energy) → sum sub-hourly values.
        # This handles sources with different sub-hourly resolutions and
        # ensures all timestamps align so cross-source comparison is correct.
        # ------------------------------------------------------------------
        def _bin_to_hours(
            watt_map: dict[datetime, float],
            wh_map: dict[datetime, float],
        ) -> tuple[dict[datetime, float], dict[datetime, float]]:
            w_buckets: dict[datetime, list[float]] = {}
            e_buckets: dict[datetime, list[float]] = {}
            for ts, val in watt_map.items():
                key = ts.replace(minute=0, second=0, microsecond=0)
                w_buckets.setdefault(key, []).append(val)
                e_buckets.setdefault(key, []).append(float(wh_map.get(ts, val)))
            w_out = {k: sum(v) / len(v) for k, v in w_buckets.items()}
            e_out = {k: sum(v) for k, v in e_buckets.items()}
            return w_out, e_out

        binned_active: list[tuple[dict, dict, float]] = []
        for (est, _), norm_w in zip(active, weights):
            bw, be = _bin_to_hours(
                dict(est.watts),  # type: ignore[arg-type]
                dict(est.wh_period),  # type: ignore[arg-type]
            )
            binned_active.append((bw, be, norm_w))

        # Rebuild all_keys from binned data
        all_keys_binned: set[datetime] = set()
        for bw, _, _ in binned_active:
            all_keys_binned |= set(bw.keys())

        watts: dict[datetime, float] = {}
        wh_period: dict[datetime, float] = {}

        for ts in all_keys_binned:
            # Only use sources that actually have this (binned) hour slot.
            # Re-normalize weights per-timestamp so a missing source doesn't
            # drag down the combined value (e.g. DWD-only far-future slots).
            ts_active_binned = [
                (bw, be, norm_w)
                for bw, be, norm_w in binned_active
                if ts in bw
            ]
            if not ts_active_binned:
                continue
            ts_total_w = sum(norm_w for _, _, norm_w in ts_active_binned)
            w_sum = 0.0
            e_sum = 0.0
            for bw, be, norm_w in ts_active_binned:
                frac = norm_w / ts_total_w
                w_sum += frac * bw[ts]
                e_sum += frac * be.get(ts, bw[ts])
            watts[ts] = w_sum
            wh_period[ts] = e_sum

        wh_days: dict[datetime, float] = {}
        for ts, val in wh_period.items():
            day_key = datetime(ts.year, ts.month, ts.day, tzinfo=ts.tzinfo)
            wh_days[day_key] = wh_days.get(day_key, 0.0) + val

        first_est = active[0][0]
        result_tz = first_est.timezone or fallback_timezone  # type: ignore[union-attr]
        LOGGER.debug(
            "[smart_pv_forecast]   Combined result: %d watt keys, tz=%r",
            len(watts), result_tz,
        )
        return Estimate(
            timezone=result_tz,
            watts=watts,
            wh_period=wh_period,
            wh_days=wh_days,
            api_rate_limit=None,
            account_type=AccountType.DWD_ICON,
        )

    def _preserve_today_past_values(
        self,
        *,
        existing: Estimate | None,
        updated: Estimate,
        local_now: datetime,
    ) -> Estimate:
        """Keep already-published values for past hours of today unchanged."""
        if existing is None:
            return updated

        cutoff_hour = local_now.replace(minute=0, second=0, microsecond=0)
        today = local_now.date()

        watts = dict(updated.watts)
        wh_period = dict(updated.wh_period)

        for ts, val in existing.watts.items():
            if ts.date() == today and ts < cutoff_hour:
                watts[ts] = float(val)

        for ts, val in existing.wh_period.items():
            if ts.date() == today and ts < cutoff_hour:
                wh_period[ts] = float(val)

        return Estimate(
            timezone=updated.timezone,
            watts=watts,
            wh_period=wh_period,
            wh_days=self._aggregate_wh_days(wh_period),
            api_rate_limit=updated.api_rate_limit,
            account_type=updated.account_type,
        )

    async def _async_update_data(self) -> Estimate:
        """Fetch radiation data from configured sources and build an estimate."""
        try:
            # Load persisted energy snapshot from disk on the very first run.
            if not self._energy_snapshot and self._energy_snapshot_version == -1:
                await self._async_init_energy_snapshot()

            local_now = dt_now()

            latitude = self.config_entry.data[CONF_LATITUDE]
            longitude = self.config_entry.data[CONF_LONGITUDE]

            enable_dwd = bool(self.config_entry.options.get(CONF_ENABLE_DWD_SOURCE, True))
            enable_open_meteo = bool(
                self.config_entry.options.get(CONF_ENABLE_OPEN_METEO_SOURCE, False)
            )
            enable_forecast_solar = bool(
                self.config_entry.options.get(CONF_ENABLE_FORECAST_SOLAR_SOURCE, False)
            )
            if not enable_dwd and not enable_open_meteo and not enable_forecast_solar:
                enable_open_meteo = True

            current_fs_key = str(
                self.config_entry.options.get(CONF_FORECAST_SOLAR_API_KEY, "")
            )
            if self._forecast_solar_client._api_key != current_fs_key:
                from homeassistant.helpers.aiohttp_client import async_get_clientsession

                self._forecast_solar_client = ForecastSolarClient(
                    async_get_clientsession(self.hass), api_key=current_fs_key
                )

            station = str(self.config_entry.options.get(CONF_MOSMIX_STATION, "")).strip()
            if enable_dwd and not station:
                raise ValueError("MOSMIX station is not configured but DWD source is enabled")

            horizon_deg = float(self.config_entry.options.get(CONF_HORIZON, 0))
            pv_arrays = self.config_entry.options.get(CONF_PV_ARRAYS, "[]")
            parsed_arrays = _parse_pv_arrays(pv_arrays)
            open_meteo_forecast_days = int(
                self.config_entry.options.get(CONF_OPEN_METEO_FORECAST_DAYS, 7)
            )

            enable_self_optimization = bool(
                self.config_entry.options.get(CONF_ENABLE_SELF_OPTIMIZATION, False)
            )
            real_power_entities = self._parse_entity_list(
                self.config_entry.options.get(CONF_REAL_POWER_ENTITIES, "[]")
            )
            battery_soc_entity = str(
                self.config_entry.options.get(CONF_BATTERY_SOC_ENTITY, "")
            ).strip()
            battery_charge_power_entity = str(
                self.config_entry.options.get(CONF_BATTERY_CHARGE_POWER_ENTITY, "")
            ).strip()
            battery_charge_limit = float(
                self.config_entry.options.get(CONF_BATTERY_CHARGE_LIMIT, 0.0)
            )
            battery_soc_threshold = float(
                self.config_entry.options.get(CONF_BATTERY_SOC_THRESHOLD, 98.0)
            )

            inverter_size_watt = self.config_entry.options.get(CONF_INVERTER_SIZE)
            if inverter_size_watt is not None and inverter_size_watt > 0:
                inverter_size_watt = float(inverter_size_watt)
            else:
                inverter_size_watt = None

            damping_morning = float(self.config_entry.options.get(CONF_DAMPING_MORNING, 0.0))
            damping_evening = float(self.config_entry.options.get(CONF_DAMPING_EVENING, 0.0))

            fingerprint_payload = {
                "latitude": latitude,
                "longitude": longitude,
                "station": station,
                "pv_arrays": parsed_arrays,
                "damping_morning": damping_morning,
                "damping_evening": damping_evening,
                "inverter_size_watt": inverter_size_watt,
                "horizon_deg": horizon_deg,
                "enable_dwd": enable_dwd,
                "enable_open_meteo": enable_open_meteo,
                "enable_forecast_solar": enable_forecast_solar,
                "source_weight_dwd": self.config_entry.options.get(CONF_SOURCE_WEIGHT_DWD, 50),
                "source_weight_open_meteo": self.config_entry.options.get(CONF_SOURCE_WEIGHT_OPEN_METEO, 50),
                "source_weight_forecast_solar": self.config_entry.options.get(CONF_SOURCE_WEIGHT_FORECAST_SOLAR, 50),
                "forecast_solar_api_key": current_fs_key,
                "enable_self_optimization": enable_self_optimization,
                "real_power_entities": real_power_entities,
                "battery_soc_entity": battery_soc_entity,
                "battery_charge_power_entity": battery_charge_power_entity,
                "battery_charge_limit": battery_charge_limit,
                "battery_soc_threshold": battery_soc_threshold,
                "open_meteo_forecast_days": open_meteo_forecast_days,
            }
            fingerprint = hashlib.sha1(
                json.dumps(fingerprint_payload, sort_keys=True, default=str).encode("utf-8")
            ).hexdigest()

            payload_dwd: Any | None = None
            payload_om: Any | None = None
            estimate_fs: Estimate | None = None

            def _cached(name: str) -> Any | None:
                entry = self._payload_cache.get(name)
                if entry is None:
                    return None
                payload, fetched_at = entry
                ttl_hours = self._source_ttl.get(name, 2)
                age = local_now - fetched_at
                if age.total_seconds() < ttl_hours * 3600:
                    LOGGER.debug(
                        "[smart_pv_forecast] Source %s: using cached payload (age %s < %dh TTL)",
                        name, str(age).split(".")[0], ttl_hours,
                    )
                    return payload
                return None

            if self._options_fingerprint is not None and fingerprint != self._options_fingerprint:
                LOGGER.warning("[smart_pv_forecast] Options changed – flushing payload cache and energy snapshot")
                self._payload_cache.clear()
                self._energy_snapshot = {}
                self._energy_snapshot_version = -1
                try:
                    self._energy_snapshot_path.unlink(missing_ok=True)
                except Exception:  # noqa: BLE001
                    pass

            radiation_tasks: list[tuple[str, Any]] = []
            if enable_dwd:
                cached = _cached("dwd")
                if cached is not None:
                    payload_dwd = cached
                else:
                    radiation_tasks.append(("dwd", self._dwd_client.fetch_radiation(station=station)))
            if enable_open_meteo:
                cached = _cached("om")
                if cached is not None:
                    payload_om = cached
                else:
                    radiation_tasks.append(
                        (
                            "om",
                            self._open_meteo_client.fetch_radiation(
                                latitude=latitude,
                                longitude=longitude,
                                pv_arrays=parsed_arrays,
                                forecast_days=open_meteo_forecast_days,
                            ),
                        )
                    )

            if radiation_tasks:
                results = await asyncio.gather(*[t for _, t in radiation_tasks], return_exceptions=True)
                for (name, _), result in zip(radiation_tasks, results):
                    if isinstance(result, Exception):
                        LOGGER.warning("Source %s failed: %s", name, result)
                    elif name == "dwd":
                        payload_dwd = result
                        self._payload_cache["dwd"] = (result, local_now)
                        self._source_data_version += 1
                    else:
                        payload_om = result
                        self._payload_cache["om"] = (result, local_now)
                        self._source_data_version += 1

            if enable_forecast_solar:
                if parsed_arrays:
                    cached_fs = _cached("fs")
                    if cached_fs is not None:
                        estimate_fs = cached_fs
                    else:
                        try:
                            fs_raw = await self._forecast_solar_client.fetch_estimate(
                                latitude=latitude,
                                longitude=longitude,
                                pv_arrays=parsed_arrays,
                            )
                            estimate_fs = Estimate(
                                timezone=fs_raw.timezone,
                                watts=fs_raw.watts,
                                wh_period=fs_raw.wh_period,
                                wh_days=fs_raw.wh_days,
                                api_rate_limit=None,
                                account_type=AccountType.DWD_ICON,
                            )
                            self._payload_cache["fs"] = (estimate_fs, local_now)
                            self._source_data_version += 1
                        except Exception as exc:  # noqa: BLE001
                            LOGGER.warning("Source forecast_solar failed: %s", exc)

            if payload_dwd is None and payload_om is None and estimate_fs is None:
                raise UpdateFailed("All configured data sources failed")

            build_kwargs: dict[str, Any] = dict(
                latitude=latitude,
                longitude=longitude,
                pv_arrays=pv_arrays,
                damping_morning=damping_morning,
                damping_evening=damping_evening,
                inverter_size_watt=inverter_size_watt,
                horizon_deg=horizon_deg,
            )

            if enable_self_optimization:
                await self._async_ensure_adaptive_model_loaded()

                base_array_watts: list[dict[datetime, float]] = []
                if payload_dwd is not None:
                    base_array_watts = build_array_watts(
                        payload_dwd,
                        latitude=latitude,
                        longitude=longitude,
                        pv_arrays=parsed_arrays,
                        damping_morning=damping_morning,
                        damping_evening=damping_evening,
                        inverter_size_watt=inverter_size_watt,
                        horizon_deg=horizon_deg,
                        array_factor_fn=None,
                    )
                elif payload_om is not None:
                    base_array_watts = build_array_watts_from_tilted_payload(
                        payload_om,
                        latitude=latitude,
                        longitude=longitude,
                        pv_arrays=parsed_arrays,
                        damping_morning=damping_morning,
                        damping_evening=damping_evening,
                        inverter_size_watt=inverter_size_watt,
                        horizon_deg=horizon_deg,
                        array_factor_fn=None,
                    )

                if base_array_watts:
                    await self._async_run_adaptive_learning(
                        local_now=local_now,
                        base_array_watts=base_array_watts,
                        pv_array_count=len(parsed_arrays),
                        real_power_entities=real_power_entities,
                        battery_soc_entity=battery_soc_entity,
                        battery_charge_power_entity=battery_charge_power_entity,
                        battery_charge_limit=battery_charge_limit,
                        battery_soc_threshold=battery_soc_threshold,
                    )
                else:
                    self._set_adaptive_status(
                        status="skipped",
                        reason="learning_needs_radiation_source",
                    )
            else:
                self._set_adaptive_status(status="disabled", reason="feature_disabled")

            array_factor_fn: Callable[[int, datetime], float] | None = None
            if enable_self_optimization:
                array_factor_fn = self._get_adaptive_factor

            estimate_dwd: Estimate | None = None
            estimate_om: Estimate | None = None

            if payload_dwd is not None:
                estimate_dwd = build_estimate(
                    payload_dwd,
                    array_factor_fn=array_factor_fn,
                    **build_kwargs,
                )

            if payload_om is not None:
                estimate_om = build_estimate_from_tilted_payload(
                    payload_om,
                    latitude=latitude,
                    longitude=longitude,
                    pv_arrays=parsed_arrays,
                    damping_morning=damping_morning,
                    damping_evening=damping_evening,
                    inverter_size_watt=inverter_size_watt,
                    horizon_deg=horizon_deg,
                    array_factor_fn=array_factor_fn,
                )

            w_dwd = int(self.config_entry.options.get(CONF_SOURCE_WEIGHT_DWD, 50))
            w_om = int(self.config_entry.options.get(CONF_SOURCE_WEIGHT_OPEN_METEO, 50))
            w_fs = int(self.config_entry.options.get(CONF_SOURCE_WEIGHT_FORECAST_SOLAR, 50))

            fallback_tz: str = "UTC"
            for candidate in (payload_dwd, payload_om):
                if candidate is not None:
                    fallback_tz = candidate.timezone
                    break
            if estimate_fs is not None and fallback_tz == "UTC":
                fallback_tz = estimate_fs.timezone

            estimate = self._combine_estimates(
                [
                    (estimate_dwd, w_dwd),
                    (estimate_om, w_om),
                    (estimate_fs, w_fs),
                ],
                fallback_timezone=fallback_tz,
            )

            estimate = self._preserve_today_past_values(
                existing=self.data,
                updated=estimate,
                local_now=local_now,
            )

            array_count = len(parsed_arrays)
            self._refresh_adaptive_snapshot(
                enabled=enable_self_optimization,
                local_now=local_now,
                array_count=array_count,
            )

            self._options_fingerprint = fingerprint
            await self._async_update_energy_snapshot(estimate)
            return estimate

        except Exception as exc:  # noqa: BLE001
            raise UpdateFailed(f"Failed to update forecast: {exc}") from exc
