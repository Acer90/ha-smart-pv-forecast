"""Admin-only Home Assistant map panel and configuration persistence."""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import voluptuous as vol

from homeassistant.components import frontend, panel_custom, websocket_api
from homeassistant.components.http import StaticPathConfig
from homeassistant.components.lovelace.resources import ResourceStorageCollection
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.util.dt import now as dt_now

from .const import CONF_PV_ARRAYS, CONF_SHADING_OBJECTS, DOMAIN
from .site import geometry_required, normalize_arrays, normalize_inverters, normalize_objects, normalize_settings, preview_time, shadow_preview, validate_inverter_assignments

PANEL_PATH = "smart-pv"
STATIC_URL = "/smart_pv_forecast_static"
DASHBOARD_MODULE = f"{STATIC_URL}/dashboard.js?v=1"


async def async_register_dashboard_resource(hass: HomeAssistant) -> None:
    lovelace = hass.data["lovelace"]
    resources = lovelace["resources"] if isinstance(lovelace, dict) else lovelace.resources
    await resources.async_get_info()
    matches = [
        item for item in resources.async_items()
        if urlsplit(item["url"]).path == f"{STATIC_URL}/dashboard.js"
    ]
    if isinstance(resources, ResourceStorageCollection):
        if not matches:
            await resources.async_create_item({"url": DASHBOARD_MODULE, "res_type": "module"})
        else:
            for item in matches:
                if item["url"] != DASHBOARD_MODULE or item["type"] != "module":
                    await resources.async_update_item(
                        item["id"], {"url": DASHBOARD_MODULE, "res_type": "module"}
                    )
    elif not any(item["type"] == "module" for item in matches):
        frontend.add_extra_js_url(hass, DASHBOARD_MODULE)


def _revision(entry: ConfigEntry) -> str:
    return hashlib.sha256(json.dumps(
        {"data": dict(entry.data), "options": dict(entry.options)},
        sort_keys=True, default=str,
    ).encode()).hexdigest()


def _site(entry: ConfigEntry) -> dict[str, Any]:
    raw_arrays = entry.options.get(CONF_PV_ARRAYS, [])
    arrays = normalize_arrays(raw_arrays) if raw_arrays not in ([], "[]") else []
    for array in arrays:
        array.setdefault("latitude", entry.data["latitude"])
        array.setdefault("longitude", entry.data["longitude"])
    return {
        "entry_id": entry.entry_id,
        "name": entry.title,
        "latitude": entry.data["latitude"],
        "longitude": entry.data["longitude"],
        "pv_arrays": arrays,
        "geometry_required": geometry_required(arrays),
        "shading_objects": normalize_objects(entry.options.get(CONF_SHADING_OBJECTS, [])),
        "inverters": normalize_inverters(entry.options.get("inverters", [])),
        "google_maps_api_key": entry.options.get("google_maps_api_key", ""),
        "settings": normalize_settings({key: entry.options[key] for key in (
            "damping_morning", "damping_evening", "horizon", "open_meteo_forecast_days",
                "daily_yield_sensor_days", "inverter_size", "forecast_low_kwh", "forecast_high_kwh",
        ) if key in entry.options}),
        "revision": _revision(entry),
    }


async def async_setup_configurator(hass: HomeAssistant) -> None:
    state = hass.data.setdefault(DOMAIN, {})
    state["loaded_entries"] = set()
    state["save_lock"] = asyncio.Lock()
    await hass.http.async_register_static_paths([
        StaticPathConfig(STATIC_URL, str(Path(__file__).parent / "frontend"), False),
    ])
    websocket_api.async_register_command(hass, websocket_get_sites)
    websocket_api.async_register_command(hass, websocket_save_site)
    websocket_api.async_register_command(hass, websocket_get_analysis)
    websocket_api.async_register_command(hass, websocket_shadow_preview)
    websocket_api.async_register_command(hass, websocket_dashboard_sites)
    websocket_api.async_register_command(hass, websocket_dashboard_analysis)
    await async_register_dashboard_resource(hass)


async def async_show_configurator(hass: HomeAssistant, entry_id: str) -> None:
    state = hass.data[DOMAIN]
    state["loaded_entries"].add(entry_id)
    if not frontend.async_panel_exists(hass, PANEL_PATH):
        await panel_custom.async_register_panel(
            hass, frontend_url_path=PANEL_PATH,
            webcomponent_name="smart-pv-configurator",
            sidebar_title="Solarprognose", sidebar_icon="mdi:solar-panel",
            module_url=f"{STATIC_URL}/configurator.js?v=0.5.0",
            require_admin=True,
        )


@callback
def async_hide_configurator(hass: HomeAssistant, entry_id: str) -> None:
    state = hass.data[DOMAIN]
    state["loaded_entries"].discard(entry_id)
    entries = hass.config_entries.async_entries(DOMAIN)
    if not entries:
        frontend.async_remove_panel(hass, PANEL_PATH)


@websocket_api.websocket_command({vol.Required("type"): "smart_pv_forecast/sites"})
@websocket_api.require_admin
@callback
def websocket_get_sites(hass: HomeAssistant, connection: websocket_api.ActiveConnection,
                        msg: dict[str, Any]) -> None:
    try:
        sites = [_site(entry) for entry in hass.config_entries.async_entries(DOMAIN)]
    except ValueError as err:
        connection.send_error(msg["id"], "invalid_site", str(err))
        return
    connection.send_result(msg["id"], sites)


@websocket_api.websocket_command({vol.Required("type"): "smart_pv_forecast/dashboard_sites"})
@callback
def websocket_dashboard_sites(hass: HomeAssistant, connection: websocket_api.ActiveConnection,
                              msg: dict[str, Any]) -> None:
    try:
        sites = []
        for entry in hass.config_entries.async_entries(DOMAIN):
            site = _site(entry)
            sites.append({
                "entry_id": site["entry_id"], "name": site["name"],
                "revision": site["revision"],
                "settings": {key: site["settings"][key] for key in
                             ("forecast_low_kwh", "forecast_high_kwh", "open_meteo_forecast_days")},
                "pv_arrays": [{
                    "name": array["name"],
                    "areas": [{"name": area["name"], "strings": area.get("strings", [])}
                              for area in array["areas"]],
                } for array in site["pv_arrays"]],
                "inverters": [{"name": inverter["name"], "power_entity": inverter["power_entity"]}
                              for inverter in site["inverters"]],
            })
    except ValueError as err:
        connection.send_error(msg["id"], "invalid_site", str(err))
        return
    connection.send_result(msg["id"], sites)


@websocket_api.websocket_command({
    vol.Required("type"): "smart_pv_forecast/save_site",
    vol.Required("entry_id"): str,
    vol.Required("revision"): str,
    vol.Required(CONF_PV_ARRAYS): list,
    vol.Required(CONF_SHADING_OBJECTS): list,
    vol.Optional("inverters"): list,
    vol.Optional("settings"): dict,
})
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_save_site(hass: HomeAssistant, connection: websocket_api.ActiveConnection,
                              msg: dict[str, Any]) -> None:
    async with hass.data[DOMAIN]["save_lock"]:
        entry = hass.config_entries.async_get_entry(msg["entry_id"])
        if entry is None or entry.domain != DOMAIN:
            connection.send_error(msg["id"], "not_found", "PV integration not found")
            return
        if _revision(entry) != msg["revision"]:
            connection.send_error(msg["id"], "conflict", "Configuration changed. Reload before saving.")
            return
        try:
            arrays = normalize_arrays(msg[CONF_PV_ARRAYS]) if msg[CONF_PV_ARRAYS] else []
            if geometry_required(arrays):
                raise ValueError("Check PV heights: 90-degree areas need a vertical extent; upper edges must not exceed 1000 m")
            objects = normalize_objects(msg[CONF_SHADING_OBJECTS])
            inverters = normalize_inverters(msg.get("inverters", entry.options.get("inverters", [])))
            validate_inverter_assignments(arrays, inverters)
            settings = normalize_settings(msg.get("settings", _site(entry)["settings"]))
            if entry.options.get("enable_open_meteo_source", True) and settings["daily_yield_sensor_days"] > settings["open_meteo_forecast_days"]:
                raise ValueError("Daily sensor days cannot exceed Open-Meteo forecast days")
            for inverter in inverters:
                for key, units in (("power_entity", ("W", "kW")), ("energy_entity", ("Wh", "kWh", "MWh"))):
                    entity = inverter[key]
                    if entity:
                        state = hass.states.get(entity)
                        if state is None or state.attributes.get("unit_of_measurement") not in units:
                            raise ValueError(f"{entity}: select a sensor with units {', '.join(units)}")
            for array in arrays:
                for area in array["areas"]:
                    for string in area["strings"]:
                        entity = string["power_entity"]
                        state = hass.states.get(entity)
                        if state is None or state.attributes.get("unit_of_measurement") not in ("W", "kW"):
                            raise ValueError(f"{entity}: select a PV string power sensor with units W or kW")
            if any("latitude" not in array for array in arrays):
                raise ValueError("Every PV array needs a map position")
        except ValueError as err:
            connection.send_error(msg["id"], "invalid_site", str(err))
            return
        options = dict(entry.options)
        options.update({CONF_PV_ARRAYS: arrays, CONF_SHADING_OBJECTS: objects, "inverters": inverters})
        options.update({key: value for key, value in settings.items() if value is not None})
        if settings["inverter_size"] is None:
            options.pop("inverter_size", None)
        # The entry update listener reloads the integration and its source caches.
        hass.config_entries.async_update_entry(entry, options=options)
        connection.send_result(msg["id"], _site(entry))


@websocket_api.websocket_command({
    vol.Required("type"): "smart_pv_forecast/analysis",
    vol.Required("entry_id"): str,
})
@websocket_api.require_admin
@callback
def websocket_get_analysis(hass: HomeAssistant, connection: websocket_api.ActiveConnection,
                           msg: dict[str, Any]) -> None:
    _send_analysis(hass, connection, msg)


@websocket_api.websocket_command({
    vol.Required("type"): "smart_pv_forecast/dashboard_analysis",
    vol.Required("entry_id"): str,
})
@callback
def websocket_dashboard_analysis(hass: HomeAssistant, connection: websocket_api.ActiveConnection,
                                 msg: dict[str, Any]) -> None:
    _send_analysis(hass, connection, msg)


def _send_analysis(hass: HomeAssistant, connection: websocket_api.ActiveConnection,
                   msg: dict[str, Any]) -> None:
    entry = hass.config_entries.async_get_entry(msg["entry_id"])
    if entry is None or entry.domain != DOMAIN:
        connection.send_error(msg["id"], "not_found", "PV integration not found")
        return
    coordinator = getattr(entry, "runtime_data", None)
    if coordinator is None:
        connection.send_error(msg["id"], "unavailable", "Forecast is not loaded")
        return
    try:
        analysis = coordinator.get_daily_analysis()
    except ValueError as err:
        connection.send_error(msg["id"], "unavailable", str(err))
        return
    if analysis["revision"] != _revision(entry):
        connection.send_error(msg["id"], "updating", "The forecast is being recalculated")
        return
    connection.send_result(msg["id"], analysis)


@websocket_api.websocket_command({
    vol.Required("type"): "smart_pv_forecast/shadow_preview",
    vol.Required("entry_id"): str,
    vol.Required(CONF_SHADING_OBJECTS): list,
    vol.Required("height"): vol.Any(int, float),
    vol.Optional("position"): dict,
    vol.Optional("when", default=None): vol.Any(str, None),
})
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_shadow_preview(hass: HomeAssistant, connection: websocket_api.ActiveConnection,
                                   msg: dict[str, Any]) -> None:
    entry = hass.config_entries.async_get_entry(msg["entry_id"])
    if entry is None or entry.domain != DOMAIN:
        connection.send_error(msg["id"], "not_found", "PV integration not found")
        return
    try:
        when = preview_time(msg.get("when"), hass.config.time_zone, dt_now())
        position = msg.get("position", {
            "latitude": entry.data["latitude"], "longitude": entry.data["longitude"],
        })
        preview = await hass.async_add_executor_job(
            shadow_preview, msg[CONF_SHADING_OBJECTS], position, msg["height"], when,
        )
    except ValueError as err:
        connection.send_error(msg["id"], "invalid_preview", str(err))
        return
    connection.send_result(msg["id"], preview)
