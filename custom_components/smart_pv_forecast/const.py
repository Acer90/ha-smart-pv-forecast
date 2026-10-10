"""Constants for the Smart PV Forecast integration."""

from __future__ import annotations

import logging

DOMAIN = "smart_pv_forecast"
LOGGER = logging.getLogger(__package__)

CONF_DECLINATION = "declination"
CONF_AZIMUTH = "azimuth"
CONF_MODULES_POWER = "modules_power"
CONF_PV_ARRAYS = "pv_arrays"
CONF_SHADING_OBJECTS = "shading_objects"
CONF_DAMPING = "damping"
CONF_DAMPING_MORNING = "damping_morning"
CONF_DAMPING_EVENING = "damping_evening"
CONF_INVERTER_SIZE = "inverter_size"

CONF_HORIZON = "horizon"

CONF_MOSMIX_STATION = "mosmix_station"
CONF_OPEN_METEO_FORECAST_DAYS = "open_meteo_forecast_days"
CONF_DAILY_YIELD_SENSOR_DAYS = "daily_yield_sensor_days"

# Multi-source options
CONF_ENABLE_DWD_SOURCE = "enable_dwd_source"
CONF_ENABLE_OPEN_METEO_SOURCE = "enable_open_meteo_source"
CONF_ENABLE_FORECAST_SOLAR_SOURCE = "enable_forecast_solar_source"
CONF_FORECAST_SOLAR_API_KEY = "forecast_solar_api_key"

# Per-source weights (0-100 each, normalized when combining)
CONF_SOURCE_WEIGHT_DWD = "source_weight_dwd"
CONF_SOURCE_WEIGHT_OPEN_METEO = "source_weight_open_meteo"
CONF_SOURCE_WEIGHT_FORECAST_SOLAR = "source_weight_forecast_solar"
