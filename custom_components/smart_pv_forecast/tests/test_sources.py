import io
import unittest
import zipfile
from datetime import datetime, timezone
from unittest.mock import AsyncMock, Mock, patch

from support import load

site = load("site")
dwd = load("dwd_icon")
om = load("open_meteo_source")
fs = load("forecast_solar_source")
analysis = load("analysis")
mosmix = load("dwd_mosmix")


class SourceTests(unittest.TestCase):
    def setUp(self):
        self.hour = datetime(2026, 6, 21, 12, tzinfo=timezone.utc)
        self.arrays = site.normalize_arrays([
            {"peak": 1000, "azimuth": 180, "tilt": 0, "latitude": 51, "longitude": 7, "height": 0},
            {"peak": 1000, "azimuth": 180, "tilt": 0, "latitude": 52, "longitude": 8, "height": 10},
        ])
        self.kwargs = dict(latitude=50, longitude=6, pv_arrays=self.arrays,
                           damping_morning=0, damping_evening=0,
                           inverter_size_watt=None, horizon_deg=0)

    def test_both_radiation_sources_apply_per_array_shadow_before_aggregation(self):
        dwd_payload = dwd.DwdIconPayload("UTC", [self.hour.isoformat()], [1000])
        om_payload = om.OpenMeteoTiltedPayload("UTC", [self.hour.isoformat()], [[1000], [1000]])
        for factor, expected in ((None, 2000), (lambda i, ts: 0 if i == 0 else 1, 1000),
                                 (lambda i, ts: 0.2, 400)):
            with self.subTest(expected=expected), patch.object(dwd, "_sun_position", return_value=(180, 45)), patch.object(om, "_sun_elevation", return_value=45):
                estimates = [
                    dwd.build_estimate(dwd_payload, shading_factor_fn=factor, **self.kwargs),
                    om.build_estimate_from_tilted_payload(om_payload, shading_factor_fn=factor, **self.kwargs),
                ]
                for estimate in estimates:
                    self.assertAlmostEqual(estimate.watts[self.hour], expected)
                    self.assertAlmostEqual(estimate.wh_period[self.hour], expected)
                    self.assertAlmostEqual(sum(estimate.wh_days.values()), expected)

    def test_sun_position_uses_individual_locations(self):
        payload = dwd.DwdIconPayload("UTC", [self.hour.isoformat()], [1000])
        with patch.object(dwd, "_sun_position", return_value=(180, 45)) as position:
            dwd.build_estimate(payload, **self.kwargs)
            self.assertEqual([(c.args[0], c.args[1]) for c in position.call_args_list], [(51, 7), (52, 8)])

    def test_inverter_and_damping_preserved(self):
        payload = om.OpenMeteoTiltedPayload("UTC", [self.hour.isoformat()], [[1000], [1000]])
        with patch.object(om, "_sun_elevation", return_value=45):
            estimate = om.build_estimate_from_tilted_payload(
                payload, **{**self.kwargs, "damping_evening": 0.1, "inverter_size_watt": 1000},
            )
        self.assertEqual(estimate.watts[self.hour], 1000)


class ForecastSolarTests(unittest.IsolatedAsyncioTestCase):
    async def test_shadows_apply_before_merge_and_per_array_coordinates_reach_api(self):
        hour = datetime(2026, 6, 21, 12, tzinfo=timezone.utc)
        client = fs.ForecastSolarClient(None)
        client._fetch_one_array = AsyncMock(side_effect=[
            fs._ArrayEstimate("UTC", {hour: 1000}, {}),
            fs._ArrayEstimate("UTC", {hour: 2000}, {}),
        ])
        arrays = site.normalize_arrays([
            {"peak": 1000, "azimuth": 180, "tilt": 30, "latitude": 51, "longitude": 7},
            {"peak": 2000, "azimuth": 270, "tilt": 30, "latitude": 52, "longitude": 8},
        ])
        estimate = await client.fetch_estimate(
            latitude=50, longitude=6, pv_arrays=arrays,
            shading_factor_fn=lambda index, ts: 0.2 if index == 0 else 1,
        )
        self.assertEqual(estimate.watts[hour], 2200)
        self.assertEqual(estimate.wh_period[hour], 2200)
        self.assertEqual(sum(estimate.wh_days.values()), 2200)
        self.assertEqual(
            [(c.kwargs["latitude"], c.kwargs["longitude"]) for c in client._fetch_one_array.call_args_list],
            [(51, 7), (52, 8)],
        )


class OpenMeteoMissingDataTests(unittest.IsolatedAsyncioTestCase):
    async def test_null_and_absent_surface_hours_are_not_zero_forecasts(self):
        times = ["2026-10-10T08:00", "2026-10-10T09:00", "2026-10-10T10:00"]
        responses = []
        for slots, values in ((times, [500, None, 0]),
                              ([times[0], times[2]], [600, 0])):
            response = AsyncMock()
            response.__aenter__.return_value = response
            response.raise_for_status = Mock()
            response.json.return_value = {"timezone": "Europe/Berlin",
                "hourly": {"time": slots, "global_tilted_irradiance": values}}
            responses.append(response)
        session = Mock(get=Mock(side_effect=responses))
        client = om.OpenMeteoClient(session)
        arrays = site.normalize_arrays([
            {"peak": 1000, "azimuth": 180, "tilt": 30},
            {"peak": 1000, "azimuth": 270, "tilt": 30},
        ])
        payload = await client.fetch_radiation(latitude=51, longitude=7, pv_arrays=arrays)
        self.assertEqual(payload.array_global_tilted_irradiance, [[500, None, 0], [600, None, 0]])
        with patch.object(om, "_sun_elevation", return_value=45):
            values = om.build_array_watts_from_tilted_payload(payload, latitude=51,
                longitude=7, pv_arrays=arrays, damping_morning=0, damping_evening=0,
                inverter_size_watt=None, horizon_deg=0)
        for surface in values:
            self.assertEqual([ts.hour for ts in surface], [8, 10])
            self.assertEqual(next(value for ts, value in surface.items() if ts.hour == 10), 0)
        missing_hour = next(iter(values[0])).replace(hour=9)
        combined = analysis.combine_arrays([(values, 50), ([{missing_hour: 800}, {missing_hour: 900}], 50)], 2)
        self.assertEqual(combined[0][missing_hour], 800)
        self.assertEqual(combined[1][missing_hour], 900)

    async def test_mosmix_missing_radiation_is_omitted_but_real_zero_is_retained(self):
        times = "".join(f"<TimeStep>2026-10-10T{hour:02d}:00:00Z</TimeStep>"
                        for hour in range(8, 12))
        rad = "".join(f"{value:>11}" for value in ("3600", "-", "3600", "0"))
        rrad = "".join(f"{value:>11}" for value in ("100", "100", "-", "100"))
        xml = f"""<kml><Document><ExtendedData><ProductDefinition><ForecastTimeSteps>
            {times}</ForecastTimeSteps></ProductDefinition></ExtendedData>
            <Placemark><ExtendedData>
            <Forecast elementName="Rad1h"><value>{rad}</value></Forecast>
            <Forecast elementName="RRad1"><value>{rrad}</value></Forecast>
            </ExtendedData></Placemark></Document></kml>"""
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("test.kml", xml)
        response = AsyncMock()
        response.__aenter__.return_value = response
        response.raise_for_status = Mock()
        response.read.return_value = buffer.getvalue()
        client = mosmix.DwdMosmixClient(Mock(get=Mock(return_value=response)))
        payload = await client.fetch_radiation(station="TEST")
        self.assertEqual([datetime.fromisoformat(ts).hour for ts in payload.hourly_time], [8, 11])
        self.assertEqual(payload.shortwave_radiation, [3600, 0])


if __name__ == "__main__":
    unittest.main()
