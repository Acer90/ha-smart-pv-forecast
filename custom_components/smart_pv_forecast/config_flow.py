"""Configure forecast and map sources; plant settings live in Solar studio."""

from __future__ import annotations

import re
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_LATITUDE, CONF_LONGITUDE, CONF_NAME
from homeassistant.core import callback

from .const import (
    CONF_ENABLE_DWD_SOURCE, CONF_ENABLE_FORECAST_SOLAR_SOURCE, CONF_ENABLE_OPEN_METEO_SOURCE,
    CONF_FORECAST_SOLAR_API_KEY, CONF_MOSMIX_STATION, CONF_PV_ARRAYS,
    CONF_SOURCE_WEIGHT_DWD, CONF_SOURCE_WEIGHT_FORECAST_SOLAR, CONF_SOURCE_WEIGHT_OPEN_METEO,
    DOMAIN,
)

RE_STATION = re.compile(r"^[A-Za-z0-9]{1,10}$")


def _source_schema(options: dict[str, Any]) -> dict[Any, Any]:
    schema: dict[Any, Any] = {}
    for key, default in ((CONF_ENABLE_DWD_SOURCE, False), (CONF_ENABLE_OPEN_METEO_SOURCE, True),
                         (CONF_ENABLE_FORECAST_SOLAR_SOURCE, False)):
        schema[vol.Optional(key, default=options.get(key, default))] = bool
    for key, default in ((CONF_SOURCE_WEIGHT_DWD, 0), (CONF_SOURCE_WEIGHT_OPEN_METEO, 100),
                         (CONF_SOURCE_WEIGHT_FORECAST_SOLAR, 50)):
        schema[vol.Optional(key, default=options.get(key, default))] = vol.All(vol.Coerce(int), vol.Range(min=0, max=100))
    for key in (CONF_MOSMIX_STATION, CONF_FORECAST_SOLAR_API_KEY, "google_maps_api_key"):
        schema[vol.Optional(key, default=options.get(key, ""))] = str
    return schema


def _source_errors(user_input: dict[str, Any]) -> dict[str, str]:
    key = user_input.get("google_maps_api_key", "").strip()
    user_input["google_maps_api_key"] = key
    if key and re.fullmatch(r"[A-Za-z0-9_-]{1,256}", key) is None:
        return {"google_maps_api_key": "invalid_google_key"}
    station = str(user_input.get(CONF_MOSMIX_STATION, "")).strip().upper()
    user_input[CONF_MOSMIX_STATION] = station
    if user_input.get(CONF_ENABLE_DWD_SOURCE) and (not station or RE_STATION.fullmatch(station) is None):
        return {CONF_MOSMIX_STATION: "invalid_station"}
    return {}


class ForecastSolarFlowHandler(ConfigFlow, domain=DOMAIN):
    VERSION = 7

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> ForecastSolarOptionFlowHandler:
        return ForecastSolarOptionFlowHandler()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _source_errors(user_input)
            if not errors:
                options = {key: value for key, value in user_input.items() if key != CONF_NAME}
                options.update({CONF_PV_ARRAYS: [], "shading_objects": [], "inverters": []})
                return self.async_create_entry(
                    title=user_input[CONF_NAME],
                    data={CONF_LATITUDE: self.hass.config.latitude, CONF_LONGITUDE: self.hass.config.longitude},
                    options=options,
                )
        defaults = {CONF_NAME: self.hass.config.location_name, **(user_input or {})}
        schema = {vol.Required(CONF_NAME, default=defaults[CONF_NAME]): str, **_source_schema(defaults)}
        return self.async_show_form(step_id="user", data_schema=vol.Schema(schema), errors=errors)


class ForecastSolarOptionFlowHandler(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _source_errors(user_input)
            if user_input.get(CONF_ENABLE_OPEN_METEO_SOURCE, True) and (
                self.config_entry.options.get("daily_yield_sensor_days", 4)
                > self.config_entry.options.get("open_meteo_forecast_days", 7)
            ):
                errors["base"] = "daily_sensors_exceed_forecast_days"
            if not errors:
                options = dict(self.config_entry.options)
                options.update(user_input)
                return self.async_create_entry(title="", data=options)
        options = {**dict(self.config_entry.options), **(user_input or {})}
        return self.async_show_form(step_id="init", data_schema=vol.Schema(_source_schema(options)), errors=errors)
