import asyncio
import importlib.util
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from support import ROOT, load
from ha_stubs import install

install()
configurator = load("configurator")
flow = load("config_flow")
coordinator_module = load("coordinator")


def entry():
    return SimpleNamespace(
        entry_id="test-entry", domain="smart_pv_forecast", title="Test PV",
        version=3, data={"latitude": 51, "longitude": 7},
        options={
            "pv_arrays": '[{"peak":1000,"azimuth":180,"tilt":30}]',
            "enable_self_optimization": True,
            "real_power_entities": '["sensor.pv"]',
            "battery_soc_entity": "sensor.soc",
            "forecast_solar_api_key": "test-only-key",
            "source_weight_open_meteo": 100,
        },
    )


def update_entry(target, **changes):
    for key, value in changes.items():
        setattr(target, key, value)


class DashboardResourceTests(unittest.IsolatedAsyncioTestCase):
    def storage(self, items):
        resources = Mock(spec=configurator.ResourceStorageCollection)
        resources.async_get_info = AsyncMock()
        resources.async_items = Mock(return_value=items)

        async def create(data):
            item = {"id": "solarstudio", "url": data["url"], "type": data["res_type"]}
            items.append(item)
            return item

        async def update(item_id, data):
            item = next(item for item in items if item["id"] == item_id)
            item.update(url=data["url"], type=data["res_type"])
            return item

        resources.async_create_item = AsyncMock(side_effect=create)
        resources.async_update_item = AsyncMock(side_effect=update)
        return resources

    async def test_storage_registration_is_persistent_and_idempotent(self):
        items = []
        resources = self.storage(items)
        hass = SimpleNamespace(data={"lovelace": SimpleNamespace(resources=resources)})
        await configurator.async_register_dashboard_resource(hass)
        await configurator.async_register_dashboard_resource(hass)
        resources.async_get_info.assert_awaited()
        resources.async_create_item.assert_awaited_once_with(
            {"url": configurator.DASHBOARD_MODULE, "res_type": "module"})
        resources.async_update_item.assert_not_awaited()
        self.assertEqual(items[0]["type"], "module")

    async def test_existing_manual_resource_is_updated_without_duplicate(self):
        unrelated = {"id": "other", "url": "/local/other.js", "type": "module"}
        items = [dict(unrelated), {
            "id": "existing", "url": f"{configurator.STATIC_URL}/dashboard.js?v=old",
            "type": "js",
        }]
        resources = self.storage(items)
        hass = SimpleNamespace(data={"lovelace": {"resources": resources}})
        await configurator.async_register_dashboard_resource(hass)
        await configurator.async_register_dashboard_resource(hass)
        resources.async_create_item.assert_not_awaited()
        resources.async_update_item.assert_awaited_once_with(
            "existing", {"url": configurator.DASHBOARD_MODULE, "res_type": "module"})
        self.assertEqual(items[0], unrelated)
        self.assertEqual(len(items), 2)

    async def test_duplicate_versions_converge_on_one_module_url(self):
        items = [
            {"id": "one", "url": f"{configurator.STATIC_URL}/dashboard.js?v=old", "type": "module"},
            {"id": "two", "url": configurator.DASHBOARD_MODULE, "type": "module"},
        ]
        resources = self.storage(items)
        await configurator.async_register_dashboard_resource(
            SimpleNamespace(data={"lovelace": SimpleNamespace(resources=resources)}))
        self.assertEqual({item["url"] for item in items}, {configurator.DASHBOARD_MODULE})
        resources.async_create_item.assert_not_awaited()

    async def test_yaml_automatically_loads_module_without_editing_yaml(self):
        items = [{"url": "/local/other.js", "type": "module"}]
        resources = SimpleNamespace(async_get_info=AsyncMock(), async_items=lambda: items)
        hass = SimpleNamespace(data={"lovelace": SimpleNamespace(resources=resources)})
        with patch.object(configurator.frontend, "add_extra_js_url") as add_module:
            await configurator.async_register_dashboard_resource(hass)
        add_module.assert_called_once_with(hass, configurator.DASHBOARD_MODULE)
        self.assertEqual(items, [{"url": "/local/other.js", "type": "module"}])

    async def test_yaml_existing_module_does_not_load_a_second_version(self):
        items = [{"url": f"{configurator.STATIC_URL}/dashboard.js?v=manual", "type": "module"}]
        resources = SimpleNamespace(async_get_info=AsyncMock(), async_items=lambda: items)
        with patch.object(configurator.frontend, "add_extra_js_url") as add_module:
            await configurator.async_register_dashboard_resource(
                SimpleNamespace(data={"lovelace": {"resources": resources}}))
        add_module.assert_not_called()

    async def test_registration_errors_are_not_hidden(self):
        resources = self.storage([])
        resources.async_create_item.side_effect = OSError("Storage unavailable")
        with self.assertRaisesRegex(OSError, "Storage unavailable"):
            await configurator.async_register_dashboard_resource(
                SimpleNamespace(data={"lovelace": SimpleNamespace(resources=resources)}))

    async def test_integration_setup_registers_resource_after_static_routes(self):
        hass = SimpleNamespace(data={}, http=SimpleNamespace(async_register_static_paths=AsyncMock()))
        with patch.object(configurator, "async_register_dashboard_resource", new_callable=AsyncMock) as register:
            await configurator.async_setup_configurator(hass)
        hass.http.async_register_static_paths.assert_awaited_once()
        register.assert_awaited_once_with(hass)
        manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
        self.assertIn("lovelace", manifest["dependencies"])


class ConfiguratorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.entry = entry()
        self.hass = SimpleNamespace(
            data={"smart_pv_forecast": {"save_lock": asyncio.Lock()}},
            config_entries=SimpleNamespace(
                async_get_entry=Mock(return_value=self.entry),
                async_entries=Mock(return_value=[self.entry]),
                async_update_entry=Mock(side_effect=update_entry),
            ),
        )
        self.connection = Mock()
        self.site = configurator._site(self.entry)

    def message(self):
        return {
            "id": 1, "entry_id": self.entry.entry_id, "revision": self.site["revision"],
            "pv_arrays": self.site["pv_arrays"], "shading_objects": [],
        }

    async def test_dashboard_sites_excludes_credentials_and_geometry(self):
        self.entry.options["google_maps_api_key"] = "private-google-key"
        configurator.websocket_dashboard_sites(self.hass, self.connection, {"id": 1})
        self.connection.send_error.assert_not_called()
        sites = self.connection.send_result.call_args.args[1]
        self.assertEqual(sites[0]["settings"]["open_meteo_forecast_days"],
                         self.site["settings"]["open_meteo_forecast_days"])
        self.assertEqual(sites[0]["entry_id"], self.entry.entry_id)
        self.assertEqual(sites[0]["pv_arrays"][0]["name"], self.site["pv_arrays"][0]["name"])
        serialized = json.dumps(sites)
        for forbidden in ("private-google-key", "test-only-key", "latitude", "longitude",
                          "google_maps_api_key", "forecast_solar_api_key", "shading_objects"):
            self.assertNotIn(forbidden, serialized)

    async def test_dashboard_analysis_uses_revision_guard(self):
        analysis = {"revision": self.site["revision"], "arrays": [], "total": None}
        self.entry.runtime_data = SimpleNamespace(get_daily_analysis=Mock(return_value=analysis))
        configurator.websocket_dashboard_analysis(
            self.hass, self.connection, {"id": 1, "entry_id": self.entry.entry_id})
        self.connection.send_result.assert_called_once_with(1, analysis)
        self.connection.reset_mock()
        analysis["revision"] = "stale"
        configurator.websocket_dashboard_analysis(
            self.hass, self.connection, {"id": 1, "entry_id": self.entry.entry_id})
        self.connection.send_error.assert_called_once_with(
            1, "updating", "The forecast is being recalculated")

    async def test_save_preserves_source_options_and_mounting(self):
        msg = self.message()
        msg["pv_arrays"][0].update(mounting_type="elevated", height=8, tilt=17)
        await configurator.websocket_save_site(self.hass, self.connection, msg)
        self.connection.send_error.assert_not_called()
        self.assertEqual(self.entry.options["forecast_solar_api_key"], "test-only-key")
        self.assertEqual(self.entry.options["pv_arrays"][0]["tilt"], 17)
        self.assertEqual(self.entry.options["pv_arrays"][0]["mounting_type"], "elevated")
        self.assertNotEqual(configurator._revision(self.entry), msg["revision"])

    async def test_forecast_colour_thresholds_save_and_reload(self):
        self.assertEqual(self.site["settings"]["forecast_low_kwh"], 15)
        self.assertEqual(self.site["settings"]["forecast_high_kwh"], 25)
        msg = self.message()
        msg["settings"] = {"forecast_low_kwh": 8.5, "forecast_high_kwh": 32}
        await configurator.websocket_save_site(self.hass, self.connection, msg)
        self.connection.send_error.assert_not_called()
        settings = configurator._site(self.entry)["settings"]
        self.assertEqual(settings["forecast_low_kwh"], 8.5)
        self.assertEqual(settings["forecast_high_kwh"], 32)
        self.assertEqual(self.entry.options["forecast_solar_api_key"], "test-only-key")

    async def test_invalid_colour_thresholds_rejected_without_saving(self):
        for low, high in ((20, 20), (30, 10), (-1, 25), (10, None), (0, 1000001), (True, 25)):
            with self.subTest(low=low, high=high):
                self.connection.reset_mock()
                msg = self.message()
                msg["settings"] = {"forecast_low_kwh": low, "forecast_high_kwh": high}
                await configurator.websocket_save_site(self.hass, self.connection, msg)
                self.assertEqual(self.connection.send_error.call_args.args[1], "invalid_site")
                self.hass.config_entries.async_update_entry.assert_not_called()

    async def test_stale_save_rejected(self):
        self.entry.options = {**self.entry.options, "source_weight_open_meteo": 50}
        await configurator.websocket_save_site(self.hass, self.connection, self.message())
        self.assertEqual(self.connection.send_error.call_args.args[1], "conflict")
        self.hass.config_entries.async_update_entry.assert_not_called()

    def string_message(self):
        msg = self.message()
        msg["pv_arrays"][0]["areas"] = [{
            "name": "Roof", "peak": 1000, "tilt": 30, "azimuth": 180, "height": 2,
            "polygon": [{"latitude": 51, "longitude": 7},
                        {"latitude": 51, "longitude": 7.0001},
                        {"latitude": 51.0001, "longitude": 7.0001}],
            "strings": [{"name": "MPPT 1", "power_entity": "sensor.string_1"},
                        {"name": "MPPT 2", "power_entity": "sensor.string_2"}],
        }]
        return msg

    async def test_strings_persist_without_changing_nominal_power(self):
        self.hass.states = SimpleNamespace(get=lambda entity: SimpleNamespace(
            attributes={"unit_of_measurement": "W" if entity.endswith("1") else "kW"}))
        msg = self.string_message()
        await configurator.websocket_save_site(self.hass, self.connection, msg)
        self.connection.send_error.assert_not_called()
        saved = configurator._site(self.entry)["pv_arrays"][0]
        self.assertEqual(saved["areas"][0]["strings"], msg["pv_arrays"][0]["areas"][0]["strings"])
        self.assertEqual(saved["peak"], 1000)

    async def test_invalid_string_assignments_rejected(self):
        for strings, unit in (
            ([{"name": "", "power_entity": "sensor.string_1"}], "W"),
            ([{"name": "A", "power_entity": "light.pv"}], "W"),
            ([{"name": "A", "power_entity": "sensor.string_1"}], "kWh"),
            ([{"name": "A", "power_entity": "sensor.string_1"}], None),
            ([{"name": "A", "power_entity": "sensor.string_1"},
              {"name": "a", "power_entity": "sensor.string_2"}], "W"),
            ([{"name": "A", "power_entity": "sensor.string_1"},
              {"name": "B", "power_entity": "sensor.string_1"}], "W"),
            ([{"name": str(i), "power_entity": f"sensor.string_{i}"} for i in range(17)], "W"),
        ):
            with self.subTest(strings=strings, unit=unit):
                self.connection.reset_mock()
                self.hass.states = SimpleNamespace(get=lambda entity: None if unit is None
                    else SimpleNamespace(attributes={"unit_of_measurement": unit}))
                msg = self.string_message()
                msg["pv_arrays"][0]["areas"][0]["strings"] = strings
                await configurator.websocket_save_site(self.hass, self.connection, msg)
                self.assertEqual(self.connection.send_error.call_args.args[1], "invalid_site")
                self.hass.config_entries.async_update_entry.assert_not_called()

    async def test_legacy_surface_gets_empty_strings(self):
        msg = self.string_message()
        del msg["pv_arrays"][0]["areas"][0]["strings"]
        await configurator.websocket_save_site(self.hass, self.connection, msg)
        self.connection.send_error.assert_not_called()
        self.assertEqual(configurator._site(self.entry)["pv_arrays"][0]["areas"][0]["strings"], [])

    def test_site_string_assignment_limit(self):
        array = self.string_message()["pv_arrays"][0]
        area = array["areas"][0]
        area["strings"] = [{"name": str(i), "power_entity": f"sensor.string_{i}"} for i in range(16)]
        array["areas"] = [dict(area) for _ in range(16)]
        normalized = configurator.normalize_arrays([array])
        self.assertEqual(sum(len(a["strings"]) for a in normalized[0]["areas"]), 256)
        array["areas"].append(dict(area))
        with self.assertRaisesRegex(ValueError, "256"):
            configurator.normalize_arrays([array])

    async def test_missing_position_and_invalid_geometry_rejected(self):
        for values in (
            {"pv_arrays": [{"peak": 1000, "azimuth": 180, "tilt": 30}]},
            {"shading_objects": [{"height": 5, "polygon": []}]},
        ):
            with self.subTest(values=values):
                self.connection.reset_mock()
                await configurator.websocket_save_site(self.hass, self.connection, {**self.message(), **values})
                self.assertEqual(self.connection.send_error.call_args.args[1], "invalid_site")
        self.hass.config_entries.async_update_entry.assert_not_called()

    async def test_foreign_entry_rejected(self):
        self.entry.domain = "another_integration"
        await configurator.websocket_save_site(self.hass, self.connection, self.message())
        self.assertEqual(self.connection.send_error.call_args.args[1], "not_found")

    def test_site_response_never_exposes_api_key(self):
        self.assertNotIn("test-only-key", json.dumps(self.site))
        self.assertEqual(self.site["pv_arrays"][0]["latitude"], 51)

    async def test_options_flow_preserves_geometry(self):
        self.entry.options.update(forecast_low_kwh=8.5, forecast_high_kwh=32)
        handler = flow.ForecastSolarOptionFlowHandler()
        handler.config_entry = self.entry
        inputs = {"enable_dwd_source": False, "enable_open_meteo_source": True,
                  "open_meteo_forecast_days": 7, "daily_yield_sensor_days": 4}
        result = await handler.async_step_init(inputs)
        self.assertEqual(result["type"], "create_entry")
        self.assertEqual(result["data"]["pv_arrays"], self.entry.options["pv_arrays"])
        self.assertEqual(result["data"]["forecast_low_kwh"], 8.5)
        self.assertEqual(result["data"]["forecast_high_kwh"], 32)

    async def test_invalid_station_preserves_entered_settings(self):
        handler = flow.ForecastSolarOptionFlowHandler()
        handler.config_entry = self.entry
        result = await handler.async_step_init({
            "enable_dwd_source": True, "mosmix_station": "bad station",
            "open_meteo_forecast_days": 7, "daily_yield_sensor_days": 4,
        })
        self.assertEqual(result["errors"]["mosmix_station"], "invalid_station")

    async def test_source_only_setup_starts_without_fictitious_array(self):
        handler = flow.ForecastSolarFlowHandler()
        handler.hass = SimpleNamespace(config=SimpleNamespace(latitude=51, longitude=7, location_name="Home"))
        result = await handler.async_step_user({
            "name": "Garage", "enable_open_meteo_source": True,
        })
        self.assertEqual(result["type"], "create_entry")
        self.assertEqual(result["options"]["pv_arrays"], [])
        self.assertEqual(result["options"]["inverters"], [])
        self.assertEqual(result["data"], {"latitude": 51, "longitude": 7})
        self.assertNotIn("enable_self_optimization", result["options"])

    async def test_invalid_forecast_horizon_returns_field_error(self):
        handler = flow.ForecastSolarOptionFlowHandler()
        handler.config_entry = self.entry
        self.entry.options.update(open_meteo_forecast_days=3, daily_yield_sensor_days=8)
        result = await handler.async_step_init({
            "enable_dwd_source": False, "enable_open_meteo_source": True,
        })
        self.assertEqual(result["errors"]["base"], "daily_sensors_exceed_forecast_days")

    def test_source_schema_does_not_include_geometry_inverters_or_forecast_settings(self):
        keys = {marker.schema for marker in flow._source_schema({})}
        self.assertEqual(keys, {
            "enable_dwd_source", "enable_open_meteo_source", "enable_forecast_solar_source",
            "forecast_solar_api_key", "mosmix_station", "source_weight_dwd",
            "source_weight_open_meteo", "source_weight_forecast_solar", "google_maps_api_key",
        })

    async def test_inverters_and_settings_save_with_sensors_and_assignments(self):
        self.hass.states = SimpleNamespace(get=lambda entity: SimpleNamespace(attributes={
            "unit_of_measurement": "W" if entity == "sensor.power" else "kWh",
        }))
        msg = self.message()
        msg["inverters"] = [{
            "id": "roof", "name": "Roof inverter", "limit_w": 1000,
            "power_entity": "sensor.power", "energy_entity": "sensor.energy",
        }]
        msg["pv_arrays"][0]["inverter_id"] = "roof"
        msg["settings"] = {"open_meteo_forecast_days": 10, "daily_yield_sensor_days": 5}
        await configurator.websocket_save_site(self.hass, self.connection, msg)
        self.connection.send_error.assert_not_called()
        self.assertEqual(self.entry.options["inverters"][0]["power_entity"], "sensor.power")
        self.assertEqual(self.entry.options["pv_arrays"][0]["inverter_id"], "roof")
        self.assertEqual(self.entry.options["open_meteo_forecast_days"], 10)
        self.assertEqual(self.entry.options["forecast_solar_api_key"], "test-only-key")

    async def test_optional_google_key_saved_preserved_and_removed(self):
        handler = flow.ForecastSolarOptionFlowHandler()
        handler.config_entry = self.entry
        result = await handler.async_step_init({"google_maps_api_key": " test-only-browser-key "})
        self.entry.options = result["data"]
        self.assertEqual(configurator._site(self.entry)["google_maps_api_key"], "test-only-browser-key")
        self.site = configurator._site(self.entry)
        msg = self.message()
        await configurator.websocket_save_site(self.hass, self.connection, msg)
        self.connection.send_error.assert_not_called()
        self.assertEqual(self.entry.options["google_maps_api_key"], "test-only-browser-key")
        result = await handler.async_step_init({"google_maps_api_key": ""})
        self.entry.options = result["data"]
        self.assertEqual(configurator._site(self.entry)["google_maps_api_key"], "")

    async def test_invalid_google_key_is_rejected_without_changing_options(self):
        handler = flow.ForecastSolarOptionFlowHandler()
        handler.config_entry = self.entry
        for key in ("key with spaces", "key?query=value", "x" * 257, "ä"):
            before = dict(self.entry.options)
            result = await handler.async_step_init({"google_maps_api_key": key})
            self.assertEqual(result["errors"]["google_maps_api_key"], "invalid_google_key")
            self.assertEqual(self.entry.options, before)

    async def test_unknown_inverter_and_wrong_sensor_unit_rejected(self):
        msg = self.message()
        msg["pv_arrays"][0]["inverter_id"] = "missing"
        await configurator.websocket_save_site(self.hass, self.connection, msg)
        self.assertEqual(self.connection.send_error.call_args.args[1], "invalid_site")
        self.hass.config_entries.async_update_entry.assert_not_called()
        self.hass.states = SimpleNamespace(get=lambda entity: SimpleNamespace(attributes={"unit_of_measurement": "%"}))
        msg["pv_arrays"][0]["inverter_id"] = ""
        msg["inverters"] = [{"id": "roof", "name": "Roof", "limit_w": 1000, "power_entity": "sensor.soc"}]
        await configurator.websocket_save_site(self.hass, self.connection, msg)
        self.assertEqual(self.connection.send_error.call_args.args[1], "invalid_site")

    def test_analysis_endpoint_rejects_stale_generation(self):
        self.entry.runtime_data = SimpleNamespace(get_daily_analysis=lambda: {"revision": "old"})
        configurator.websocket_get_analysis(self.hass, self.connection, {"id": 2, "entry_id": "test-entry"})
        self.assertEqual(self.connection.send_error.call_args.args[1], "updating")

    def test_empty_entry_is_editable_in_studio(self):
        self.entry.options["pv_arrays"] = []
        self.assertEqual(configurator._site(self.entry)["pv_arrays"], [])

    async def test_empty_site_can_save_inverters_before_first_array(self):
        self.entry.options["pv_arrays"] = []
        self.site = configurator._site(self.entry)
        msg = self.message()
        msg["inverters"] = [{"id": "first", "name": "Inverter", "limit_w": 5000}]
        await configurator.websocket_save_site(self.hass, self.connection, msg)
        self.connection.send_error.assert_not_called()
        self.assertEqual(self.entry.options["pv_arrays"], [])
        self.assertEqual(self.entry.options["inverters"][0]["id"], "first")

    async def test_incomplete_vertical_area_is_rejected_on_save_not_hidden(self):
        msg = self.message()
        msg["pv_arrays"][0]["areas"] = [{
            "name": "Facade", "peak": 1000, "tilt": 90, "azimuth": 180, "height": 2,
            "polygon": [{"latitude": 51, "longitude": 7},
                        {"latitude": 51, "longitude": 7.0001},
                        {"latitude": 51.0001, "longitude": 7.0001}],
        }]
        await configurator.websocket_save_site(self.hass, self.connection, msg)
        self.assertEqual(self.connection.send_error.call_args.args[1], "invalid_site")
        self.hass.config_entries.async_update_entry.assert_not_called()

        msg["pv_arrays"][0]["areas"][0]["vertical_height"] = 2
        self.connection.reset_mock()
        await configurator.websocket_save_site(self.hass, self.connection, msg)
        self.connection.send_error.assert_not_called()
        self.assertEqual(self.entry.options["pv_arrays"][0]["areas"][0]["vertical_height"], 2)
        self.assertFalse(self.connection.send_result.call_args.args[1]["geometry_required"])

    async def test_shadow_preview_uses_unsaved_objects_without_persisting(self):
        self.hass.config = SimpleNamespace(time_zone="Europe/Berlin")
        self.hass.async_add_executor_job = AsyncMock(side_effect=lambda func, *args: func(*args))
        before = json.dumps(self.entry.options, sort_keys=True)
        await configurator.websocket_shadow_preview(self.hass, self.connection, {
            "id": 7, "entry_id": self.entry.entry_id, "height": 6,
            "when": "2026-06-21T12:00",
            "shading_objects": [{"name": "Unsaved wall", "solid": True, "transmission": 0,
                "line": [{"latitude": 51, "longitude": 6.9999, "height": 12},
                         {"latitude": 51, "longitude": 7.0001, "height": 12}]}],
        })
        result = self.connection.send_result.call_args.args[1]
        self.assertEqual(result["local_time"], "2026-06-21T12:00")
        self.assertEqual(result["height"], 6)
        self.assertEqual(result["shadows"][0]["name"], "Unsaved wall")
        self.assertNotIn("forecast_solar_api_key", result)
        self.assertEqual(json.dumps(self.entry.options, sort_keys=True), before)
        self.hass.config_entries.async_update_entry.assert_not_called()

    async def test_preview_rejects_wrong_entry_and_invalid_inputs(self):
        self.hass.config = SimpleNamespace(time_zone="Europe/Berlin")
        self.hass.async_add_executor_job = AsyncMock(side_effect=lambda func, *args: func(*args))
        msg = {"id": 7, "entry_id": self.entry.entry_id, "height": -1, "shading_objects": []}
        await configurator.websocket_shadow_preview(self.hass, self.connection, msg)
        self.assertEqual(self.connection.send_error.call_args.args[1], "invalid_preview")
        self.entry.domain = "other_integration"
        await configurator.websocket_shadow_preview(self.hass, self.connection, msg)
        self.assertEqual(self.connection.send_error.call_args.args[1], "not_found")

    async def test_preview_defaults_to_actual_time_in_ha_timezone(self):
        self.hass.config = SimpleNamespace(time_zone="Europe/Berlin")
        self.hass.async_add_executor_job = AsyncMock(side_effect=lambda func, *args: func(*args))
        now = datetime(2026, 6, 21, 23, 45, 59, tzinfo=timezone.utc)
        with patch.object(configurator, "dt_now", return_value=now):
            await configurator.websocket_shadow_preview(self.hass, self.connection, {
                "id": 7, "entry_id": self.entry.entry_id, "height": 0, "shading_objects": [],
            })
        result = self.connection.send_result.call_args.args[1]
        self.assertEqual(result["local_time"], "2026-06-22T01:45")
        self.assertEqual(datetime.fromisoformat(result["time"]).astimezone(timezone.utc), now)
        self.assertEqual(result["timezone"], "Europe/Berlin")


class CoordinatorTests(unittest.IsolatedAsyncioTestCase):
    async def test_energy_dashboard_hours_are_chronological_with_real_zero_preserved(self):
        hass = SimpleNamespace(config=SimpleNamespace(path=lambda name: name))
        coordinator = coordinator_module.ForecastSolarDataUpdateCoordinator(hass, entry())
        coordinator._async_save_energy_snapshot = AsyncMock()
        slots = {
            datetime(2026, 10, 10, 14, tzinfo=timezone.utc): 500,
            datetime(2026, 10, 10, 0, tzinfo=timezone.utc): 0,
            datetime(2026, 10, 10, 10, tzinfo=timezone.utc): 1500,
            datetime(2026, 10, 10, 12, tzinfo=timezone.utc): 2000,
        }
        estimate = coordinator_module.Estimate("UTC", slots, slots, {})
        await coordinator._async_update_energy_snapshot(estimate)
        hours = coordinator.get_energy_wh_hours()
        times = [datetime.fromisoformat(key) for key in hours]
        self.assertEqual(times, sorted(times))
        self.assertEqual(list(hours.values()), [0, 1500, 2000, 500])
        self.assertEqual(len(hours), 4)

    async def test_snapshot_key_changes_are_not_masked_by_zero_tolerance(self):
        hass = SimpleNamespace(config=SimpleNamespace(path=lambda name: name))
        coordinator = coordinator_module.ForecastSolarDataUpdateCoordinator(hass, entry())
        coordinator._async_save_energy_snapshot = AsyncMock()
        hour = datetime(2026, 10, 10, 12, tzinfo=timezone.utc)
        for slots in ({hour: 100, hour.replace(hour=13): 0},
                      {hour: 100, hour.replace(hour=14): 0}):
            await coordinator._async_update_energy_snapshot(
                coordinator_module.Estimate("UTC", slots, slots, {}))
        hours = coordinator.get_energy_wh_hours()
        self.assertNotIn(hour.replace(hour=13).isoformat(), hours)
        self.assertIn(hour.replace(hour=14).isoformat(), hours)
        self.assertEqual(coordinator._async_save_energy_snapshot.await_count, 2)

    async def test_energy_export_orders_real_instants_across_dst_offsets(self):
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("Europe/Berlin")
        slots = {
            datetime(2026, 10, 25, 2, tzinfo=timezone.utc).astimezone(tz): 300,
            datetime(2026, 10, 25, 1, tzinfo=timezone.utc): 200,
            datetime(2026, 10, 25, 0, tzinfo=timezone.utc): 100,
        }
        current = entry()
        hass = SimpleNamespace(config=SimpleNamespace(path=lambda name: name),
                               config_entries=SimpleNamespace(async_get_entry=lambda entry_id: current))
        coordinator = coordinator_module.ForecastSolarDataUpdateCoordinator(hass, current)
        current.runtime_data = coordinator
        coordinator._async_save_energy_snapshot = AsyncMock()
        await coordinator._async_update_energy_snapshot(coordinator_module.Estimate("Europe/Berlin", slots, slots, {}))
        exported = await load("energy").async_get_solar_forecast(hass, current.entry_id)
        times = [datetime.fromisoformat(key).timestamp() for key in exported["wh_hours"]]
        self.assertEqual(times, sorted(times))
        self.assertEqual(list(exported["wh_hours"].values()), [100, 200, 300])

    async def test_snapshot_zero_transitions_update_below_existing_noise_threshold(self):
        coordinator = coordinator_module.ForecastSolarDataUpdateCoordinator(
            SimpleNamespace(config=SimpleNamespace(path=lambda name: name)), entry())
        coordinator._async_save_energy_snapshot = AsyncMock()
        hour = datetime(2026, 10, 10, 12, tzinfo=timezone.utc)
        for value in (0, 5, 0):
            slots = {hour: value}
            await coordinator._async_update_energy_snapshot(coordinator_module.Estimate("UTC", slots, slots, {}))
            self.assertEqual(coordinator.get_energy_wh_hours()[hour.isoformat()], value)
        self.assertEqual(coordinator._async_save_energy_snapshot.await_count, 3)

    async def test_missing_vertical_extent_is_setup_required_without_source_fetches(self):
        current = entry()
        current.options.update(enable_dwd_source=False, enable_open_meteo_source=True)
        current.options["pv_arrays"] = [{
            "peak": 1000, "azimuth": 180, "tilt": 90,
            "areas": [{"polygon": [{"latitude": 51, "longitude": 7},
                                   {"latitude": 51, "longitude": 7.0001},
                                   {"latitude": 51.0001, "longitude": 7.0001}]}],
        }]
        hass = SimpleNamespace(config=SimpleNamespace(
            path=lambda name: str(Path(tempfile.gettempdir()) / name), time_zone="UTC",
        ))
        coordinator = coordinator_module.ForecastSolarDataUpdateCoordinator(hass, current)
        coordinator._async_init_energy_snapshot = AsyncMock()
        coordinator._open_meteo_client.fetch_radiation = AsyncMock()
        estimate = await coordinator._async_update_data()
        self.assertTrue(coordinator.setup_required)
        self.assertEqual(estimate.watts, {})
        coordinator._open_meteo_client.fetch_radiation.assert_not_awaited()
        self.assertTrue(configurator._site(current)["geometry_required"])

    async def test_empty_setup_does_not_fetch_sources_or_publish_forecasts(self):
        current = entry()
        current.options.update(
            pv_arrays=[], enable_dwd_source=False, enable_open_meteo_source=True,
            enable_forecast_solar_source=True,
        )
        hass = SimpleNamespace(config=SimpleNamespace(
            path=lambda name: str(Path(tempfile.gettempdir()) / name),
            time_zone="Europe/Berlin",
        ))
        coordinator = coordinator_module.ForecastSolarDataUpdateCoordinator(hass, current)
        coordinator._async_init_energy_snapshot = AsyncMock()
        coordinator._open_meteo_client.fetch_radiation = AsyncMock()
        coordinator._dwd_client.fetch_radiation = AsyncMock()
        coordinator._forecast_solar_client.fetch_estimate = AsyncMock()
        estimate = await coordinator._async_update_data()
        self.assertTrue(coordinator.setup_required)
        self.assertEqual(estimate.watts, {})
        self.assertEqual(estimate.timezone, "Europe/Berlin")
        coordinator._open_meteo_client.fetch_radiation.assert_not_awaited()
        coordinator._dwd_client.fetch_radiation.assert_not_awaited()
        coordinator._forecast_solar_client.fetch_estimate.assert_not_awaited()
        with self.assertRaisesRegex(ValueError, "not available"):
            coordinator.get_daily_analysis()

    async def test_individual_surface_parameters_and_logical_array_analysis(self):
        current = entry()
        polygon = [
            {"latitude": 51, "longitude": 7},
            {"latitude": 51, "longitude": 7.0001},
            {"latitude": 51.0001, "longitude": 7.0001},
        ]
        current.options.update(
            enable_dwd_source=False, enable_open_meteo_source=True,
            pv_arrays=[
                {"name": "Roof", "peak": 3000, "azimuth": 180, "tilt": 30, "areas": [
                    {"name": "East", "polygon": polygon, "peak": 1000,
                     "azimuth": 90, "tilt": 10, "height": 6},
                    {"name": "West", "polygon": polygon, "peak": 2000,
                     "azimuth": 270, "tilt": 40, "height": 7},
                ]},
                {"name": "Garage", "peak": 500, "azimuth": 180, "tilt": 30},
            ],
        )
        hass = SimpleNamespace(config=SimpleNamespace(
            path=lambda name: str(Path(tempfile.gettempdir()) / name),
            time_zone="Europe/Berlin",
        ))
        coordinator = coordinator_module.ForecastSolarDataUpdateCoordinator(hass, current)
        hour = datetime(2026, 6, 21, 12, tzinfo=timezone.utc)
        payload = load("open_meteo_source").OpenMeteoTiltedPayload(
            "UTC", [hour.isoformat()], [[1000], [500], [1000]],
        )
        coordinator._open_meteo_client.fetch_radiation = AsyncMock(return_value=payload)
        coordinator._async_init_energy_snapshot = AsyncMock()
        coordinator._async_update_energy_snapshot = AsyncMock()
        with patch.object(coordinator_module, "dt_now", return_value=hour), patch(
            "smart_pv_test.open_meteo_source._sun_elevation", return_value=45
        ), patch.object(coordinator_module.ObjectShading, "factor",
                        side_effect=lambda index, ts: [0.5, 0.8, 0.4][index]):
            estimate = await coordinator._async_update_data()
            analysis = coordinator.get_daily_analysis()
        requested = coordinator._open_meteo_client.fetch_radiation.call_args.kwargs["pv_arrays"]
        self.assertEqual([(a["azimuth"], a["tilt"]) for a in requested],
                         [(90, 10), (270, 40), (180, 30)])
        self.assertEqual([a["height"] for a in requested[:2]], [6, 7])
        self.assertAlmostEqual(estimate.watts[hour], 1500)
        self.assertEqual([a["name"] for a in analysis["arrays"]], ["Roof", "Garage"])
        self.assertEqual(analysis["timezone"], "Europe/Berlin")
        roof = analysis["arrays"][0]["hours"][14]
        self.assertAlmostEqual(roof["baseline_w"], 2000)
        self.assertAlmostEqual(roof["shaded_w"], 1300)
        self.assertAlmostEqual(roof["reduction_percent"], 35)
        self.assertAlmostEqual(analysis["arrays"][1]["hours"][14]["shaded_w"], 200)

    async def test_no_learning_and_shading_is_wired_in_update(self):
        current = entry()
        current.options.update(enable_dwd_source=False, enable_open_meteo_source=True)
        hass = SimpleNamespace(config=SimpleNamespace(path=lambda name: str(Path(tempfile.gettempdir()) / name)))
        coordinator = coordinator_module.ForecastSolarDataUpdateCoordinator(hass, current)
        self.assertFalse(hasattr(coordinator, "_adaptive_store"))
        hour = datetime(2026, 6, 21, 12, tzinfo=timezone.utc)
        payload = load("open_meteo_source").OpenMeteoTiltedPayload("UTC", [hour.isoformat()], [[1000]])
        coordinator._open_meteo_client.fetch_radiation = AsyncMock(return_value=payload)
        coordinator._async_init_energy_snapshot = AsyncMock()
        coordinator._async_update_energy_snapshot = AsyncMock()
        with patch.object(coordinator_module, "dt_now", return_value=hour), patch(
            "smart_pv_test.open_meteo_source._sun_elevation", return_value=45
        ):
            estimate = await coordinator._async_update_data()
        self.assertEqual(estimate.watts[hour], 1000)
        self.assertTrue(coordinator._options_fingerprint)
        self.assertEqual(coordinator.get_energy_wh_hours(), {})

    async def test_snapshot_reused_only_for_same_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            current = entry()
            async def executor(function):
                return function()
            hass = SimpleNamespace(
                config=SimpleNamespace(path=lambda name: str(Path(directory) / name)),
                async_add_executor_job=executor,
            )
            coordinator = coordinator_module.ForecastSolarDataUpdateCoordinator(hass, current)
            snapshot = {datetime.now(timezone.utc).isoformat(): 500}
            await coordinator._async_save_energy_snapshot(snapshot)
            await coordinator._async_init_energy_snapshot()
            self.assertEqual(coordinator._energy_snapshot, snapshot)
            current.options = {**current.options, "pv_arrays": [{"peak": 2000, "azimuth": 180, "tilt": 30}]}
            coordinator._energy_snapshot = {}
            await coordinator._async_init_energy_snapshot()
            self.assertEqual(coordinator._energy_snapshot, {})


class MigrationTests(unittest.IsolatedAsyncioTestCase):
    def migration_module(self):
        spec = importlib.util.spec_from_file_location(
            "smart_pv_test.migration", ROOT / "__init__.py",
            submodule_search_locations=[str(ROOT)],
        )
        migration = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = migration
        for name in ("const", "site", "coordinator", "configurator"):
            sys.modules[f"{spec.name}.{name}"] = load(name)
        spec.loader.exec_module(migration)
        return migration

    async def test_options_reload_keeps_panel_registered_and_removes_it_only_after_deletion(self):
        integration = self.migration_module()
        current = entry()
        current.add_update_listener = Mock()
        current.async_on_unload = Mock()
        state = {"loaded_entries": {current.entry_id}}
        coordinator = SimpleNamespace(async_config_entry_first_refresh=AsyncMock())
        async def executor(function):
            return function()
        with tempfile.TemporaryDirectory() as directory:
            snapshot = Path(directory) / f"smart_pv_forecast_{current.entry_id}_energy_snapshot.json"
            snapshot.write_text("{}")
            hass = SimpleNamespace(
                data={"smart_pv_forecast": state},
                config=SimpleNamespace(path=lambda name: str(Path(directory) / name)),
                async_add_executor_job=executor,
                config_entries=SimpleNamespace(
                    async_entries=Mock(return_value=[current]),
                    async_unload_platforms=AsyncMock(return_value=True),
                    async_forward_entry_setups=AsyncMock(),
                ),
            )
            with patch.object(configurator.frontend, "async_remove_panel") as remove, patch.object(
                configurator.frontend, "async_panel_exists", return_value=True
            ), patch.object(configurator.panel_custom, "async_register_panel", new_callable=AsyncMock) as register, patch.object(
                integration, "ForecastSolarDataUpdateCoordinator", return_value=coordinator
            ):
                async def reload_entry(identifier):
                    self.assertEqual(identifier, current.entry_id)
                    self.assertTrue(await integration.async_unload_entry(hass, current))
                    self.assertEqual(state["loaded_entries"], set())
                    remove.assert_not_called()
                    self.assertTrue(await integration.async_setup_entry(hass, current))
                hass.config_entries.async_reload = AsyncMock(side_effect=reload_entry)
                await integration._async_options_updated(hass, current)
                self.assertFalse(snapshot.exists())
                self.assertEqual(state["loaded_entries"], {current.entry_id})
                remove.assert_not_called()
                register.assert_not_called()
                # A disabled/unloaded but still configured entry keeps the planning UI.
                await integration.async_unload_entry(hass, current)
                remove.assert_not_called()
                hass.config_entries.async_entries.return_value = []
                await integration.async_remove_entry(hass, current)
                remove.assert_called_once_with(hass, "smart-pv")

    async def test_deleting_one_of_multiple_entries_keeps_panel(self):
        integration = self.migration_module()
        current = entry()
        other = SimpleNamespace(entry_id="other")
        hass = SimpleNamespace(data={"smart_pv_forecast": {"loaded_entries": {current.entry_id, other.entry_id}}},
            config_entries=SimpleNamespace(async_entries=Mock(return_value=[other])))
        with patch.object(configurator.frontend, "async_remove_panel") as remove:
            await integration.async_remove_entry(hass, current)
            remove.assert_not_called()
            self.assertEqual(hass.data["smart_pv_forecast"]["loaded_entries"], {other.entry_id})

    async def test_removing_never_loaded_entry_needs_no_panel_state(self):
        await self.migration_module().async_remove_entry(SimpleNamespace(data={}), entry())

    async def test_v6_vertical_geometry_remains_editable_without_guessed_dimensions(self):
        migration = self.migration_module()
        current = entry()
        current.version = 6
        current.options["pv_arrays"] = [{
            "peak": 1000, "azimuth": 180, "tilt": 90, "height": 4,
            "areas": [{"polygon": [{"latitude": 51, "longitude": 7},
                                   {"latitude": 51, "longitude": 7.0001},
                                   {"latitude": 51.0001, "longitude": 7.0001}]}],
        }]
        hass = SimpleNamespace(config_entries=SimpleNamespace(
            async_update_entry=Mock(side_effect=update_entry),
        ))
        self.assertTrue(await migration.async_migrate_entry(hass, current))
        self.assertEqual(current.version, 7)
        self.assertIsNone(current.options["pv_arrays"][0]["areas"][0]["vertical_height"])
        self.assertTrue(configurator._site(current)["geometry_required"])

    async def test_v4_polygon_becomes_closed_height_line_and_keeps_transmission(self):
        migration = self.migration_module()
        current = entry()
        current.version = 4
        polygon = [
            {"latitude": 51, "longitude": 7},
            {"latitude": 51, "longitude": 7.0001},
            {"latitude": 51.0001, "longitude": 7.0001},
        ]
        current.options["shading_objects"] = [
            {"name": "House", "polygon": polygon, "height": 12, "transmission": 0.2},
        ]
        hass = SimpleNamespace(config_entries=SimpleNamespace(
            async_update_entry=Mock(side_effect=update_entry),
        ))
        self.assertTrue(await migration.async_migrate_entry(hass, current))
        self.assertEqual(current.version, 7)
        obj = current.options["shading_objects"][0]
        self.assertNotIn("polygon", obj)
        self.assertEqual(obj["line"], [{**point, "height": 12} for point in polygon + [polygon[0]]])
        self.assertEqual(obj["transmission"], 0.2)
        self.assertTrue(obj["solid"])
        self.assertEqual(current.options["inverters"], [])

    async def test_v3_keeps_arrays_removes_learning_options_entities_and_snapshot(self):
        migration = self.migration_module()
        with tempfile.TemporaryDirectory() as directory:
            current = entry()
            snapshot = Path(directory) / f"smart_pv_forecast_{current.entry_id}_energy_snapshot.json"
            snapshot.write_text("{}")
            async def executor(function):
                return function()
            registry = Mock()
            entities = [
                SimpleNamespace(unique_id="test-entry_adaptive_factor", entity_id="sensor.old_adaptive"),
                SimpleNamespace(unique_id="test-entry_energy_production_today", entity_id="sensor.energy_today"),
            ]
            hass = SimpleNamespace(
                config=SimpleNamespace(path=lambda name: str(Path(directory) / name)),
                config_entries=SimpleNamespace(async_update_entry=Mock(side_effect=update_entry)),
                async_add_executor_job=executor,
            )
            with patch.object(migration.er, "async_get", return_value=registry), patch.object(
                migration.er, "async_entries_for_config_entry", return_value=entities
            ):
                self.assertTrue(await migration.async_migrate_entry(hass, current))
            self.assertEqual(current.version, 7)
            self.assertEqual(current.options["pv_arrays"][0]["peak"], 1000)
            self.assertEqual(current.options["pv_arrays"][0]["height"], 0)
            self.assertEqual(current.options["pv_arrays"][0]["latitude"], 51)
            self.assertNotIn("enable_self_optimization", current.options)
            self.assertNotIn("real_power_entities", current.options)
            self.assertFalse(snapshot.exists())
            registry.async_remove.assert_called_once_with("sensor.old_adaptive")

    async def test_oldest_entry_migrates_through_all_versions(self):
        migration = self.migration_module()
        current = entry()
        current.version = 1
        current.options = {"modules power": 3000, "azimuth": 270, "declination": 20, "damping": 0.1}
        with tempfile.TemporaryDirectory() as directory:
            async def executor(function):
                return function()
            hass = SimpleNamespace(
                config=SimpleNamespace(path=lambda name: str(Path(directory) / name)),
                config_entries=SimpleNamespace(async_update_entry=Mock(side_effect=update_entry)),
                async_add_executor_job=executor,
            )
            self.assertTrue(await migration.async_migrate_entry(hass, current))
        self.assertEqual(current.version, 7)
        self.assertEqual(current.options["pv_arrays"][0]["peak"], 3000)
        self.assertEqual(current.options["pv_arrays"][0]["azimuth"], 270)
        self.assertEqual(current.options["damping_evening"], 0.1)
        self.assertEqual(current.options["damping_morning"], 0.1)


if __name__ == "__main__":
    unittest.main()
