"""Config flow for Smart PV Forecast integration."""

from __future__ import annotations

import re
import json
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_LATITUDE, CONF_LONGITUDE, CONF_NAME
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv, selector

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
    CONF_DAILY_YIELD_SENSOR_DAYS,
    CONF_MOSMIX_STATION,
    CONF_OPEN_METEO_FORECAST_DAYS,
    CONF_PV_ARRAYS,
    CONF_REAL_POWER_ENTITIES,
    CONF_SOURCE_WEIGHT_DWD,
    CONF_SOURCE_WEIGHT_FORECAST_SOLAR,
    CONF_SOURCE_WEIGHT_OPEN_METEO,
    DOMAIN,
)

RE_STATION = re.compile(r"^[A-Za-z0-9]{1,10}$")


def _pretty_json_text(value: Any, fallback: str) -> str:
    """Return a readable JSON string for form defaults."""
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return fallback
        try:
            return json.dumps(json.loads(value), indent=2)
        except json.JSONDecodeError:
            return value

    if value is None:
        return fallback

    try:
        return json.dumps(value, indent=2)
    except (TypeError, ValueError):
        return fallback


def _validate_pv_arrays(value: str) -> str:
    """Validate PV arrays JSON.

    Expected format:
    [
      {"peak": 5000, "azimuth": 180, "tilt": 30},
      ...
    ]

    Note: In UI texts we avoid curly braces due to Home Assistant translation formatting.
    """
    if value.strip() in ("", "[]"):
        raise vol.Invalid("invalid_format")

    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as err:
        raise vol.Invalid("invalid_json") from err

    if not isinstance(parsed, list) or not parsed:
        raise vol.Invalid("invalid_format")

    for item in parsed:
        if not isinstance(item, dict):
            raise vol.Invalid("invalid_format")

        for key in ("peak", "azimuth", "tilt"):
            if key not in item:
                raise vol.Invalid("invalid_format")

        try:
            peak = float(item["peak"])
            azimuth = float(item["azimuth"])
            tilt = float(item["tilt"])
        except (TypeError, ValueError) as err:
            raise vol.Invalid("invalid_format") from err

        if peak <= 0:
            raise vol.Invalid("invalid_range")
        if not (0 <= azimuth <= 360):
            raise vol.Invalid("invalid_range")
        if not (0 <= tilt <= 90):
            raise vol.Invalid("invalid_range")

    return json.dumps(parsed)


def _validate_entity_id_list(value: str) -> str:
    """Validate JSON list of entity IDs and return normalized JSON."""
    text = (value or "").strip()
    if not text:
        return "[]"

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as err:
        raise vol.Invalid("invalid_json") from err

    if not isinstance(parsed, list):
        raise vol.Invalid("invalid_format")

    normalized: list[str] = []
    for item in parsed:
        entity_id = str(item).strip()
        if not entity_id:
            continue
        try:
            normalized.append(cv.entity_id(entity_id))
        except vol.Invalid as err:
            raise vol.Invalid("invalid_entity_id") from err

    return json.dumps(normalized)


def _validate_optional_entity_id(value: str) -> str:
    """Validate optional entity ID and return normalized value."""
    entity_id = (value or "").strip()
    if not entity_id:
        return ""
    try:
        return cv.entity_id(entity_id)
    except vol.Invalid as err:
        raise vol.Invalid("invalid_entity_id") from err


class ForecastSolarFlowHandler(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Smart PV Forecast."""

    VERSION = 3

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> ForecastSolarOptionFlowHandler:
        """Get the options flow for this handler."""
        return ForecastSolarOptionFlowHandler()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle a flow initiated by the user."""
        errors: dict[str, str] = {}
        if user_input is not None:
            station = str(user_input.get(CONF_MOSMIX_STATION, "")).strip().upper()
            if station and RE_STATION.match(station) is None:
                errors[CONF_MOSMIX_STATION] = "invalid_station"

            try:
                pv_arrays = _validate_pv_arrays(str(user_input.get(CONF_PV_ARRAYS, "")))
            except vol.Invalid:
                errors[CONF_PV_ARRAYS] = "invalid_pv_arrays"

            if errors:
                return self.async_show_form(
                    step_id="user",
                    data_schema=self._user_schema(user_input),
                    errors=errors,
                )

            return self.async_create_entry(
                title=user_input[CONF_NAME],
                data={
                    CONF_LATITUDE: user_input[CONF_LATITUDE],
                    CONF_LONGITUDE: user_input[CONF_LONGITUDE],
                },
                options={
                    CONF_PV_ARRAYS: pv_arrays,
                    CONF_MOSMIX_STATION: station,
                    CONF_ENABLE_DWD_SOURCE: False,
                    CONF_ENABLE_OPEN_METEO_SOURCE: True,
                    CONF_SOURCE_WEIGHT_DWD: 0,
                    CONF_SOURCE_WEIGHT_OPEN_METEO: 100,
                    CONF_ENABLE_SELF_OPTIMIZATION: False,
                    CONF_REAL_POWER_ENTITIES: "[]",
                    CONF_BATTERY_SOC_ENTITY: "",
                    CONF_BATTERY_CHARGE_POWER_ENTITY: "",
                    CONF_BATTERY_CHARGE_LIMIT: 0.0,
                    CONF_BATTERY_SOC_THRESHOLD: 98.0,
                    CONF_OPEN_METEO_FORECAST_DAYS: 7,
                    CONF_DAILY_YIELD_SENSOR_DAYS: 4,
                },
            )

        return self.async_show_form(
            step_id="user",
            data_schema=self._user_schema(user_input),
            errors=errors,
        )

    def _user_schema(self, user_input: dict[str, Any] | None) -> vol.Schema:
        name_default = self.hass.config.location_name
        lat_default = self.hass.config.latitude
        lon_default = self.hass.config.longitude
        station_default = ""
        pv_arrays_default = _pretty_json_text(
            [{"peak": 5000, "azimuth": 180, "tilt": 30}],
            "[]",
        )

        if user_input:
            name_default = user_input.get(CONF_NAME, name_default)
            lat_default = user_input.get(CONF_LATITUDE, lat_default)
            lon_default = user_input.get(CONF_LONGITUDE, lon_default)
            station_default = user_input.get(CONF_MOSMIX_STATION, station_default)
            pv_arrays_default = user_input.get(CONF_PV_ARRAYS, pv_arrays_default)

        return vol.Schema(
            {
                vol.Required(CONF_NAME, default=name_default): str,
                vol.Required(CONF_LATITUDE, default=lat_default): cv.latitude,
                vol.Required(CONF_LONGITUDE, default=lon_default): cv.longitude,
                vol.Optional(CONF_MOSMIX_STATION, default=station_default): str,
                # Keep as plain string in schema; validate on submit to avoid schema serialization issues.
                vol.Required(CONF_PV_ARRAYS, default=pv_arrays_default): selector.TextSelector(
                    selector.TextSelectorConfig(multiline=True)
                ),
            }
        )


class ForecastSolarOptionFlowHandler(OptionsFlowWithReload):
    """Handle options."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        errors: dict[str, str] = {}
        if user_input is not None:
            enable_dwd = bool(user_input.get(CONF_ENABLE_DWD_SOURCE, True))
            enable_open_meteo = bool(user_input.get(CONF_ENABLE_OPEN_METEO_SOURCE, True))
            enable_self_optimization = bool(
                user_input.get(CONF_ENABLE_SELF_OPTIMIZATION, False)
            )

            forecast_days = int(user_input.get(CONF_OPEN_METEO_FORECAST_DAYS, 7))
            daily_sensor_days = int(user_input.get(CONF_DAILY_YIELD_SENSOR_DAYS, 4))

            station = str(user_input.get(CONF_MOSMIX_STATION, "")).strip().upper()
            if enable_dwd and (not station or RE_STATION.match(station) is None):
                errors[CONF_MOSMIX_STATION] = "invalid_station"

            parsed_arrays_for_count: list[Any] = []

            if CONF_PV_ARRAYS in user_input:
                try:
                    user_input[CONF_PV_ARRAYS] = _validate_pv_arrays(
                        user_input[CONF_PV_ARRAYS]
                    )
                    parsed_arrays_for_count = json.loads(user_input[CONF_PV_ARRAYS])
                except vol.Invalid:
                    errors[CONF_PV_ARRAYS] = "invalid_pv_arrays"

            try:
                user_input[CONF_REAL_POWER_ENTITIES] = _validate_entity_id_list(
                    str(user_input.get(CONF_REAL_POWER_ENTITIES, "[]"))
                )
            except vol.Invalid:
                errors[CONF_REAL_POWER_ENTITIES] = "invalid_entity_id"

            try:
                user_input[CONF_BATTERY_SOC_ENTITY] = _validate_optional_entity_id(
                    str(user_input.get(CONF_BATTERY_SOC_ENTITY, ""))
                )
            except vol.Invalid:
                errors[CONF_BATTERY_SOC_ENTITY] = "invalid_entity_id"

            try:
                user_input[CONF_BATTERY_CHARGE_POWER_ENTITY] = _validate_optional_entity_id(
                    str(user_input.get(CONF_BATTERY_CHARGE_POWER_ENTITY, ""))
                )
            except vol.Invalid:
                errors[CONF_BATTERY_CHARGE_POWER_ENTITY] = "invalid_entity_id"

            if enable_self_optimization and not errors:
                real_entities = json.loads(user_input[CONF_REAL_POWER_ENTITIES])
                if not real_entities:
                    errors[CONF_REAL_POWER_ENTITIES] = "missing_real_power_entities"
                elif parsed_arrays_for_count and len(real_entities) != len(parsed_arrays_for_count):
                    errors[CONF_REAL_POWER_ENTITIES] = "invalid_entity_count"

            if enable_open_meteo and daily_sensor_days > forecast_days:
                errors[CONF_DAILY_YIELD_SENSOR_DAYS] = "daily_sensors_exceed_forecast_days"

            if errors:
                return self.async_show_form(
                    step_id="init",
                    data_schema=self._schema(),
                    errors=errors,
                )

            if enable_dwd:
                user_input[CONF_MOSMIX_STATION] = station
            return self.async_create_entry(title="", data=user_input)

        return self.async_show_form(
            step_id="init",
            data_schema=self._schema(),
            errors=errors,
        )

    def _schema(self) -> vol.Schema:
        pv_arrays_default = _pretty_json_text(
            self.config_entry.options.get(CONF_PV_ARRAYS, "[]"),
            "[]",
        )
        real_power_entities_default = _pretty_json_text(
            self.config_entry.options.get(CONF_REAL_POWER_ENTITIES, "[]"),
            "[]",
        )

        return vol.Schema(
            {
                vol.Optional(
                    CONF_ENABLE_FORECAST_SOLAR_SOURCE,
                    default=self.config_entry.options.get(CONF_ENABLE_FORECAST_SOLAR_SOURCE, False),
                ): bool,
                vol.Optional(
                    CONF_FORECAST_SOLAR_API_KEY,
                    default=self.config_entry.options.get(CONF_FORECAST_SOLAR_API_KEY, ""),
                ): str,
                vol.Optional(
                    CONF_SOURCE_WEIGHT_FORECAST_SOLAR,
                    default=self.config_entry.options.get(CONF_SOURCE_WEIGHT_FORECAST_SOLAR, 50),
                ): vol.All(vol.Coerce(int), vol.Range(min=0, max=100)),
                vol.Optional(
                    CONF_ENABLE_DWD_SOURCE,
                    default=self.config_entry.options.get(CONF_ENABLE_DWD_SOURCE, False),
                ): bool,
                vol.Optional(
                    CONF_ENABLE_OPEN_METEO_SOURCE,
                    default=self.config_entry.options.get(CONF_ENABLE_OPEN_METEO_SOURCE, True),
                ): bool,
                vol.Optional(
                    CONF_SOURCE_WEIGHT_DWD,
                    default=self.config_entry.options.get(CONF_SOURCE_WEIGHT_DWD, 0),
                ): vol.All(vol.Coerce(int), vol.Range(min=0, max=100)),
                vol.Optional(
                    CONF_SOURCE_WEIGHT_OPEN_METEO,
                    default=self.config_entry.options.get(CONF_SOURCE_WEIGHT_OPEN_METEO, 100),
                ): vol.All(vol.Coerce(int), vol.Range(min=0, max=100)),
                vol.Optional(
                    CONF_OPEN_METEO_FORECAST_DAYS,
                    default=self.config_entry.options.get(CONF_OPEN_METEO_FORECAST_DAYS, 7),
                ): vol.All(vol.Coerce(int), vol.Range(min=1, max=16)),
                vol.Optional(
                    CONF_DAILY_YIELD_SENSOR_DAYS,
                    default=self.config_entry.options.get(CONF_DAILY_YIELD_SENSOR_DAYS, 4),
                ): vol.All(vol.Coerce(int), vol.Range(min=2, max=16)),
                vol.Optional(
                    CONF_MOSMIX_STATION,
                    default=self.config_entry.options.get(CONF_MOSMIX_STATION, ""),
                ): str,
                vol.Required(
                    CONF_PV_ARRAYS,
                    default=pv_arrays_default,
                ): selector.TextSelector(selector.TextSelectorConfig(multiline=True)),
                vol.Optional(
                    CONF_DAMPING_MORNING,
                    default=self.config_entry.options.get(CONF_DAMPING_MORNING, 0.0),
                ): vol.Coerce(float),
                vol.Optional(
                    CONF_DAMPING_EVENING,
                    default=self.config_entry.options.get(CONF_DAMPING_EVENING, 0.0),
                ): vol.Coerce(float),
                vol.Optional(
                    CONF_INVERTER_SIZE,
                    description={
                        "suggested_value": self.config_entry.options.get(
                            CONF_INVERTER_SIZE
                        )
                    },
                ): vol.Coerce(int),
                vol.Optional(
                    CONF_HORIZON,
                    default=self.config_entry.options.get(CONF_HORIZON, 0),
                ): vol.All(vol.Coerce(int), vol.Range(min=0, max=90)),
                vol.Optional(
                    CONF_ENABLE_SELF_OPTIMIZATION,
                    default=self.config_entry.options.get(CONF_ENABLE_SELF_OPTIMIZATION, False),
                ): bool,
                vol.Optional(
                    CONF_REAL_POWER_ENTITIES,
                    default=real_power_entities_default,
                ): selector.TextSelector(selector.TextSelectorConfig(multiline=True)),
                vol.Optional(
                    CONF_BATTERY_SOC_ENTITY,
                    default=self.config_entry.options.get(CONF_BATTERY_SOC_ENTITY, ""),
                ): str,
                vol.Optional(
                    CONF_BATTERY_CHARGE_POWER_ENTITY,
                    default=self.config_entry.options.get(CONF_BATTERY_CHARGE_POWER_ENTITY, ""),
                ): str,
                vol.Optional(
                    CONF_BATTERY_CHARGE_LIMIT,
                    default=self.config_entry.options.get(CONF_BATTERY_CHARGE_LIMIT, 0.0),
                ): vol.Coerce(float),
                vol.Optional(
                    CONF_BATTERY_SOC_THRESHOLD,
                    default=self.config_entry.options.get(CONF_BATTERY_SOC_THRESHOLD, 98.0),
                ): vol.All(vol.Coerce(float), vol.Range(min=70, max=100)),
            }
        )
