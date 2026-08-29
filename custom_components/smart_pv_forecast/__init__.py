"""The Forecast.Solar integration."""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import (
    CONF_DAMPING,
    CONF_DAMPING_EVENING,
    CONF_DAMPING_MORNING,
    CONF_MODULES_POWER,
    CONF_PV_ARRAYS,
)
from .coordinator import ForecastSolarConfigEntry, ForecastSolarDataUpdateCoordinator

PLATFORMS = [Platform.SENSOR]


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

    return True


async def async_setup_entry(
    hass: HomeAssistant, entry: ForecastSolarConfigEntry
) -> bool:
    """Set up Forecast.Solar from a config entry."""
    coordinator = ForecastSolarDataUpdateCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: ForecastSolarConfigEntry
) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
