"""The Forecast.Solar integration."""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType
from pathlib import Path

from .const import (
    CONF_DAMPING,
    CONF_DAMPING_EVENING,
    CONF_DAMPING_MORNING,
    CONF_MODULES_POWER,
    CONF_PV_ARRAYS,
    CONF_SHADING_OBJECTS,
    DOMAIN,
)
from .coordinator import ForecastSolarConfigEntry, ForecastSolarDataUpdateCoordinator
from .configurator import async_setup_configurator, async_show_configurator, async_hide_configurator
from .site import normalize_arrays, normalize_objects

PLATFORMS = [Platform.SENSOR]

LEGACY_ADAPTIVE_OPTIONS = (
    "enable_self_optimization", "real_power_entities", "battery_soc_entity",
    "battery_charge_power_entity", "battery_charge_limit", "battery_soc_threshold",
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the shared map and authenticated configuration commands."""
    await async_setup_configurator(hass)
    return True


async def async_migrate_entry(
    hass: HomeAssistant, entry: ForecastSolarConfigEntry
) -> bool:
    """Migrate old config entry."""

    if entry.version == 1:
        new_options = entry.options.copy()
        new_options |= {
            CONF_MODULES_POWER: new_options.pop("modules power"),
            CONF_DAMPING_MORNING: new_options.get(CONF_DAMPING, 0.0),
            CONF_DAMPING_EVENING: new_options.pop(CONF_DAMPING, 0.0),
        }

        hass.config_entries.async_update_entry(
            entry, data=entry.data, options=new_options, version=2
        )

    if entry.version == 2:
        # Migrate from single-orientation config (modules_power/azimuth/declination)
        # to multi-array config (pv_arrays).
        new_options = entry.options.copy()

        peak = new_options.get(CONF_MODULES_POWER)
        azimuth = new_options.get("azimuth")
        tilt = new_options.get("declination")

        if peak is not None and azimuth is not None and tilt is not None:
            new_options[CONF_PV_ARRAYS] = [
                {
                    "peak": float(peak),
                    "azimuth": float(azimuth),
                    "tilt": float(tilt),
                }
            ]

        # Remove legacy keys if present
        new_options.pop(CONF_MODULES_POWER, None)
        new_options.pop("azimuth", None)
        new_options.pop("declination", None)

        hass.config_entries.async_update_entry(
            entry, data=entry.data, options=new_options, version=3
        )

    if entry.version == 3:
        new_options = dict(entry.options)
        arrays = normalize_arrays(new_options.get(CONF_PV_ARRAYS, []))
        for array in arrays:
            array.setdefault("latitude", entry.data["latitude"])
            array.setdefault("longitude", entry.data["longitude"])
        new_options[CONF_PV_ARRAYS] = arrays
        new_options[CONF_SHADING_OBJECTS] = normalize_objects(new_options.get(CONF_SHADING_OBJECTS, []))
        for key in LEGACY_ADAPTIVE_OPTIONS:
            new_options.pop(key, None)
        await Store(hass, 1, f"{DOMAIN}_{entry.entry_id}_adaptive_model").async_remove()
        snapshot = Path(hass.config.path(f"{DOMAIN}_{entry.entry_id}_energy_snapshot.json"))
        await hass.async_add_executor_job(lambda: snapshot.unlink(missing_ok=True))
        registry = er.async_get(hass)
        for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
            if entity.unique_id.startswith(f"{entry.entry_id}_adaptive_"):
                registry.async_remove(entity.entity_id)
        hass.config_entries.async_update_entry(entry, options=new_options, version=4)

    if entry.version == 4:
        options = dict(entry.options)
        options[CONF_PV_ARRAYS] = normalize_arrays(options[CONF_PV_ARRAYS])
        options[CONF_SHADING_OBJECTS] = normalize_objects(options.get(CONF_SHADING_OBJECTS, []))
        for obj in options[CONF_SHADING_OBJECTS]:
            obj.pop("polygon", None)
        hass.config_entries.async_update_entry(entry, options=options, version=5)

    if entry.version == 5:
        options = dict(entry.options)
        options.setdefault("inverters", [])
        hass.config_entries.async_update_entry(entry, options=options, version=6)

    if entry.version == 6:
        options = dict(entry.options)
        raw_arrays = options.get(CONF_PV_ARRAYS, [])
        options[CONF_PV_ARRAYS] = normalize_arrays(raw_arrays) if raw_arrays not in ([], "[]") else []
        hass.config_entries.async_update_entry(entry, options=options, version=7)

    return True


async def async_setup_entry(
    hass: HomeAssistant, entry: ForecastSolarConfigEntry
) -> bool:
    """Set up Forecast.Solar from a config entry."""
    coordinator = ForecastSolarDataUpdateCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await async_show_configurator(hass, entry.entry_id)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: ForecastSolarConfigEntry
) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        async_hide_configurator(hass, entry.entry_id)
    return unloaded


async def _async_options_updated(hass: HomeAssistant, entry: ForecastSolarConfigEntry) -> None:
    snapshot = Path(hass.config.path(f"{DOMAIN}_{entry.entry_id}_energy_snapshot.json"))
    await hass.async_add_executor_job(lambda: snapshot.unlink(missing_ok=True))
    await hass.config_entries.async_reload(entry.entry_id)


async def async_remove_entry(hass: HomeAssistant, entry: ForecastSolarConfigEntry) -> None:
    """Remove the shared panel only when the final configured entry is deleted."""
    if DOMAIN in hass.data:
        async_hide_configurator(hass, entry.entry_id)
