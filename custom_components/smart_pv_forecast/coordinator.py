"""DataUpdateCoordinator for the Smart PV Forecast integration."""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from typing import TypeAlias

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_LATITUDE, CONF_LONGITUDE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util.dt import now as dt_now

from .const import (
    CONF_DAMPING_EVENING,
    CONF_DAMPING_MORNING,
    CONF_ENABLE_DWD_SOURCE,
    CONF_ENABLE_FORECAST_SOLAR_SOURCE,
    CONF_ENABLE_OPEN_METEO_SOURCE,
    CONF_FORECAST_SOLAR_API_KEY,
    CONF_HORIZON,
    CONF_INVERTER_SIZE,
    CONF_MOSMIX_STATION,
    CONF_OPEN_METEO_FORECAST_DAYS,
    CONF_PV_ARRAYS,
    CONF_SHADING_OBJECTS,
    CONF_SOURCE_WEIGHT_DWD,
    CONF_SOURCE_WEIGHT_FORECAST_SOLAR,
    CONF_SOURCE_WEIGHT_OPEN_METEO,
    DOMAIN,
    LOGGER,
)

from .dwd_icon import build_array_watts
from .site import ObjectShading, flatten_arrays, geometry_required, normalize_arrays, normalize_inverters, normalize_objects, validate_inverter_assignments
from .analysis import apply_inverter_limits, combine_arrays, daily_analysis, estimate_from_arrays, group_arrays
from .dwd_mosmix import DwdMosmixClient
from .forecast_solar_source import ForecastSolarClient
from .models import Estimate, AccountType
from .open_meteo_source import (
    OpenMeteoClient,
    build_array_watts_from_tilted_payload,
)

ForecastSolarConfigEntry: TypeAlias = ConfigEntry["ForecastSolarDataUpdateCoordinator"]

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
        self._analysis_data: dict[str, Any] | None = None
        self.setup_required = False

    def get_daily_analysis(self) -> dict[str, Any]:
        if self._analysis_data is None:
            raise ValueError("Forecast analysis is not available yet")
        return daily_analysis(
            when=dt_now(),
            forecast_day_count=int(self.config_entry.options.get("open_meteo_forecast_days", 7)),
            **self._analysis_data,
        )

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

    def _configuration_fingerprint(self) -> str:
        return hashlib.sha256(json.dumps(
            {"data": dict(self.config_entry.data), "options": dict(self.config_entry.options)},
            sort_keys=True, default=str,
        ).encode()).hexdigest()

    async def _async_init_energy_snapshot(self) -> None:
        """Load persisted energy snapshot from disk (called once at first update)."""
        try:
            def _do_load() -> dict[str, float]:
                import json as _json
                from datetime import date as _date
                if not self._energy_snapshot_path.exists():
                    return {}
                stored = _json.loads(self._energy_snapshot_path.read_text(encoding="utf-8"))
                if not isinstance(stored, dict) or stored.get("fingerprint") != self._configuration_fingerprint():
                    LOGGER.debug("[smart_pv_forecast] Snapshot configuration changed - discarding")
                    return {}
                data = stored.get("wh_hours")
                if not isinstance(data, dict) or not data:
                    return {}
                today_str = _date.today().isoformat()
                if not any(k[:10] >= today_str for k in data):
                    LOGGER.debug("[smart_pv_forecast] Energy snapshot is stale – discarding")
                    return {}
                LOGGER.debug(
                    "[smart_pv_forecast] Loaded energy snapshot from disk (%d keys)", len(data)
                )
                return {k: float(data[k]) for k in sorted(
                    data, key=lambda key: datetime.fromisoformat(key).timestamp()
                )}

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
                    _json.dumps(
                        {"fingerprint": self._configuration_fingerprint(), "wh_hours": snapshot},
                        indent=None, separators=(",", ":"),
                    ),
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
            for ts, val in sorted(estimate.wh_period.items(), key=lambda item: item[0].timestamp())
        }
        changed = list(candidate) != list(self._energy_snapshot) or any(
            (value == 0) != (self._energy_snapshot[key] == 0)
            or abs(value - self._energy_snapshot[key]) > 20.0
            for key, value in candidate.items()
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
            self.setup_required = pv_arrays in ([], "[]")
            if self.setup_required:
                self._analysis_data = None
                LOGGER.info("PV setup required: draw arrays in the Smart PV configurator")
                return estimate_from_arrays([], getattr(self.hass.config, "time_zone", "UTC"))
            parsed_arrays = normalize_arrays(pv_arrays)
            if geometry_required(parsed_arrays):
                self.setup_required = True
                self._analysis_data = None
                LOGGER.warning("PV setup required: check upper-edge heights and vertical extents in Solarstudio")
                return estimate_from_arrays([], getattr(self.hass.config, "time_zone", "UTC"))
            surfaces, parents = flatten_arrays(parsed_arrays)
            inverters = normalize_inverters(self.config_entry.options.get("inverters", []))
            validate_inverter_assignments(parsed_arrays, inverters)
            objects = normalize_objects(self.config_entry.options.get(CONF_SHADING_OBJECTS, []))
            shading = ObjectShading(surfaces, objects, latitude, longitude)
            open_meteo_forecast_days = int(
                self.config_entry.options.get(CONF_OPEN_METEO_FORECAST_DAYS, 7)
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
                "shading_objects": objects,
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
                "open_meteo_forecast_days": open_meteo_forecast_days,
            }
            fingerprint = hashlib.sha1(
                json.dumps(fingerprint_payload, sort_keys=True, default=str).encode("utf-8")
            ).hexdigest()

            payload_dwd: Any | None = None
            payload_om: Any | None = None
            fs_raw: Any | None = None

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
                                pv_arrays=surfaces,
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
                        fs_raw = cached_fs
                    else:
                        try:
                            fs_raw = await self._forecast_solar_client.fetch_estimate(
                                latitude=latitude,
                                longitude=longitude,
                                pv_arrays=surfaces,
                            )
                            self._payload_cache["fs"] = (fs_raw, local_now)
                            self._source_data_version += 1
                        except Exception as exc:  # noqa: BLE001
                            LOGGER.warning("Source forecast_solar failed: %s", exc)

            if payload_dwd is None and payload_om is None and fs_raw is None:
                raise UpdateFailed("All configured data sources failed")

            build_kwargs: dict[str, Any] = dict(
                latitude=latitude,
                longitude=longitude,
                pv_arrays=surfaces,
                damping_morning=damping_morning,
                damping_evening=damping_evening,
                inverter_size_watt=inverter_size_watt,
                horizon_deg=horizon_deg,
            )

            sources: list[tuple[list[dict[datetime, float]], float]] = []
            source_names: list[str] = []

            if payload_dwd is not None:
                sources.append((build_array_watts(payload_dwd, **build_kwargs),
                                float(self.config_entry.options.get(CONF_SOURCE_WEIGHT_DWD, 50))))
                source_names.append("DWD")

            if payload_om is not None:
                sources.append((build_array_watts_from_tilted_payload(payload_om, **build_kwargs),
                                float(self.config_entry.options.get(CONF_SOURCE_WEIGHT_OPEN_METEO, 50))))
                source_names.append("Open-Meteo")
            if fs_raw is not None:
                sources.append((fs_raw.array_wh_period,
                                float(self.config_entry.options.get(CONF_SOURCE_WEIGHT_FORECAST_SOLAR, 50))))
                source_names.append("forecast.solar")

            fallback_tz: str = "UTC"
            for candidate in (payload_dwd, payload_om):
                if candidate is not None:
                    fallback_tz = candidate.timezone
                    break
            if fs_raw is not None and fallback_tz == "UTC":
                fallback_tz = fs_raw.timezone

            baseline = apply_inverter_limits(combine_arrays(sources, len(surfaces)), surfaces, inverters)
            shaded = [
                {ts: value * shading.factor(index, ts) for ts, value in values.items()}
                for index, values in enumerate(baseline)
            ]
            grouped_baseline = group_arrays(baseline, parents, len(parsed_arrays))
            grouped_shaded = group_arrays(shaded, parents, len(parsed_arrays))
            estimate = estimate_from_arrays(grouped_shaded, fallback_tz)
            self._analysis_data = {
                "arrays": parsed_arrays, "baseline": grouped_baseline,
                "shaded": grouped_shaded, "timezone": getattr(self.hass.config, "time_zone", fallback_tz),
                "revision": self._configuration_fingerprint(), "sources": source_names,
            }

            estimate = self._preserve_today_past_values(
                existing=self.data,
                updated=estimate,
                local_now=local_now,
            )

            self._options_fingerprint = fingerprint
            await self._async_update_energy_snapshot(estimate)
            return estimate

        except Exception as exc:  # noqa: BLE001
            raise UpdateFailed(f"Failed to update forecast: {exc}") from exc
