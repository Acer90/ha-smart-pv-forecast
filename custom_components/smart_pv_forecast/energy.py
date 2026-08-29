"""Energy platform."""

from __future__ import annotations

from homeassistant.core import HomeAssistant

from .coordinator import ForecastSolarDataUpdateCoordinator


async def async_get_solar_forecast(
    hass: HomeAssistant, config_entry_id: str
) -> dict[str, dict[str, float | int]] | None:
    """Get solar forecast for a config entry ID.

    Returns a stable snapshot that only changes when fresh source data has been
    fetched from the API (every 2–6 h). This prevents the HA Energy Dashboard
    from drawing a new dashed forecast line on every hourly coordinator tick.
    """
    if (
        entry := hass.config_entries.async_get_entry(config_entry_id)
    ) is None or not isinstance(entry.runtime_data, ForecastSolarDataUpdateCoordinator):
        return None

    wh_hours = entry.runtime_data.get_energy_wh_hours()
    if not wh_hours:
        return None

    return {"wh_hours": wh_hours}
