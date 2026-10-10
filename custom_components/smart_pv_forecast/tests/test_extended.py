import unittest
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from support import load
from test_site import array, point

site = load("site")
analysis = load("analysis")


def area(east, peak=1000, **values):
    return {
        "name": "Surface", "peak": peak, "azimuth": 180, "tilt": 30,
        "height": 0, "mounting_type": "elevated",
        "polygon": [point(east - 5, 0), point(east + 5, 0),
                    point(east + 5, 10), point(east - 5, 10)],
        **values,
    }


def line(**values):
    return {
        "name": "Contour",
        "line": [{**point(-10, 20), "height": 50}, {**point(0, 20), "height": 50}],
        "solid": True, "transmission": 0, **values,
    }


class ExtendedGeometryTests(unittest.TestCase):
    def test_line_height_interpolated_at_ray_intersection(self):
        points = [(-10, 10, 2), (10, 10, 18)]
        sample = (0, 0, 0, 1)
        self.assertTrue(site.line_blocks(points, 0, 30, sample))
        self.assertFalse(site.line_blocks(points, 0, 50, sample))
        self.assertTrue(site.line_blocks(points, 45, 50, sample))
        self.assertFalse(site.line_blocks(points, 180, 30, sample))
        self.assertFalse(site.line_blocks(points, 0, 30, (0, 0, 8, 1)))

    def test_collinear_line_and_positive_distance(self):
        self.assertTrue(site.line_blocks([(0, 10, 20), (0, 20, 10)], 0, 30, (0, 0, 0, 1)))
        self.assertFalse(site.line_blocks([(0, -20, 20), (0, -10, 10)], 0, 30, (0, 0, 0, 1)))

    def test_partial_area_shading_instead_of_single_point(self):
        arrays = site.normalize_arrays([array(areas=[area(0)])])
        shading = site.ObjectShading(arrays, site.normalize_objects([line()]), 0, 0)
        factor = shading.at_sun(0, 0, 30)
        self.assertGreater(factor, 0)
        self.assertLess(factor, 1)
        self.assertAlmostEqual(sum(p[3] for p in shading.samples[0]), 1)

    def test_multiple_surfaces_have_individual_installation_values(self):
        arrays = site.normalize_arrays([array(areas=[
            area(0, peak=1200, height=4, tilt=15, azimuth=90),
            area(30, peak=800, height=8, tilt=70, azimuth=270, mounting_type="facade"),
        ])])
        flat, parents = site.flatten_arrays(arrays)
        self.assertEqual(parents, [0, 0])
        self.assertEqual(arrays[0]["peak"], 2000)
        self.assertEqual([a["peak"] for a in flat], [1200, 800])
        self.assertEqual([a["tilt"] for a in flat], [15, 70])
        self.assertEqual([a["azimuth"] for a in flat], [90, 270])
        self.assertEqual([a["height"] for a in flat], [4, 8])
        self.assertEqual(flat[1]["mounting_type"], "facade")
        self.assertNotEqual(flat[0]["longitude"], flat[1]["longitude"])

    def test_concave_surface_samples_stay_inside_and_weights_sum_to_one(self):
        polygon = [point(0, 0), point(4, 0), point(4, 1), point(1, 1), point(1, 4), point(0, 4)]
        arrays = site.normalize_arrays([array(areas=[area(0, polygon=polygon)])])
        lat, lon = arrays[0]["latitude"], arrays[0]["longitude"]
        samples = site.surface_samples(arrays[0], lat, lon)
        local = [site._local_point(lat, lon, p) for p in polygon]
        for x, y, _, _ in samples:
            self.assertTrue(site._contains_origin([(px - x, py - y) for px, py in local]))
        self.assertAlmostEqual(sum(p[3] for p in samples), 1)

    def test_surface_and_line_validation(self):
        for invalid in (
            array(areas=[area(0, peak=0)]),
            array(areas=[area(0, height=-1)]),
            array(areas=[area(0, polygon=[point(0, 0), point(1, 1)])]),
            array(areas=[area(0)] * 33),
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                site.normalize_arrays([invalid])
        for invalid in (
            line(line=[{**point(0, 0), "height": 10}]),
            line(line=[{**point(0, 0), "height": 10}] * 2),
            line(line=[{**point(0, 0), "height": -1}, {**point(1, 1), "height": 5}]),
            line(solid="false"),
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                site.normalize_objects([invalid])

    def test_legacy_polygon_becomes_closed_line_with_point_heights(self):
        obj = site.normalize_objects([{
            "name": "Old house", "height": 12, "transmission": 0.2,
            "polygon": area(0)["polygon"],
        }])[0]
        self.assertEqual(len(obj["line"]), 5)
        self.assertEqual(obj["line"][0], obj["line"][-1])
        self.assertEqual([p["height"] for p in obj["line"]], [12] * 5)
        self.assertEqual(obj["transmission"], 0.2)
        self.assertTrue(obj["solid"])


class SeasonTests(unittest.TestCase):
    def object(self, **values):
        return site.normalize_objects([line(solid=False, **values)])[0]

    def test_leaf_on_leaf_off_and_boundaries(self):
        obj = self.object(reduction_leaf_on=0.9, reduction_leaf_off=0.5)
        for when, expected in ((date(2026, 3, 31), 0.5), (date(2026, 4, 1), 0.1),
                               (date(2026, 10, 31), 0.1), (date(2026, 11, 1), 0.5)):
            self.assertAlmostEqual(site.object_transmission(obj, when), expected)

    def test_cross_year_and_manual_override(self):
        obj = self.object(leaf_on="11-01", leaf_off="04-01")
        self.assertAlmostEqual(site.object_transmission(obj, date(2026, 1, 1)), 0.1)
        self.assertAlmostEqual(site.object_transmission(obj, date(2026, 6, 1)), 0.5)
        obj["foliage_mode"] = "leaf_off"
        self.assertEqual(site.object_transmission(obj, date(2026, 1, 1)), 0.5)
        obj["foliage_mode"] = "leaf_on"
        self.assertAlmostEqual(site.object_transmission(obj, date(2026, 6, 1)), 0.1)

    def test_solid_ignores_season(self):
        obj = site.normalize_objects([line(transmission=0.05)])[0]
        self.assertEqual(site.object_transmission(obj, date(2026, 6, 1)), 0.05)
        self.assertEqual(site.object_transmission(obj, date(2026, 12, 1)), 0.05)

    def test_shading_uses_season_for_actual_timestamp(self):
        obj = self.object(line=[{**point(-10, 10), "height": 20}, {**point(10, 10), "height": 20}])
        shading = site.ObjectShading(site.normalize_arrays([array()]), [obj], 0, 0)
        self.assertAlmostEqual(shading.at_sun(0, 0, 30, date(2026, 6, 1)), 0.1)
        self.assertEqual(shading.at_sun(0, 0, 30, date(2026, 12, 1)), 0.5)

    def test_invalid_dates_modes_and_percentages(self):
        for values in ({"leaf_on": "02-30"}, {"leaf_on": "04-1 "},
                       {"leaf_on": "04-01", "leaf_off": "04-01"},
                       {"foliage_mode": "unknown"}, {"reduction_leaf_on": 1.1}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                self.object(**values)


class AnalysisTests(unittest.TestCase):
    def test_forecast_day_count_matches_configured_horizon(self):
        start = datetime(2026, 10, 9, tzinfo=timezone.utc)
        slots = {start + timedelta(days=offset): 1000 for offset in range(16)}
        for count in (1, 3, 7, 10, 16):
            report = analysis.daily_analysis([{"name": "Roof"}], [slots], [slots], "UTC",
                start, "revision", ["Test"], forecast_day_count=count)
            self.assertEqual(len(report["forecast_days"]), count)
            self.assertEqual(report["forecast_days"][-1]["date"],
                             (start + timedelta(days=count - 1)).date().isoformat())

    def test_total_and_seven_day_forecast_require_common_array_slots(self):
        start = datetime(2026, 10, 9, tzinfo=timezone.utc)
        next_day = start + timedelta(days=1)
        base = [{start: 1000, next_day: 2000}, {start: 3000}]
        shaded = [{start: 500, next_day: 1000}, {start: 1500}]
        report = analysis.daily_analysis([{"name": "A"}, {"name": "B"}], base, shaded, "UTC",
                                        start, "revision", ["Test"])
        self.assertEqual(report["total"]["hours"][0]["baseline_w"], 4000)
        self.assertEqual(report["total"]["hours"][0]["shaded_w"], 2000)
        self.assertIsNone(report["total"]["hours"][1]["baseline_w"])
        self.assertEqual(len(report["forecast_days"]), 7)
        self.assertEqual(report["forecast_days"][0]["shaded_wh"], 2000)
        self.assertEqual(report["forecast_days"][0]["covered_hours"], 1)
        self.assertEqual(report["forecast_days"][0]["expected_hours"], 24)
        self.assertIsNone(report["forecast_days"][1]["shaded_wh"])
        self.assertEqual(report["forecast_days"][1]["covered_hours"], 0)

    def test_future_forecast_uses_ha_local_days_not_utc_dates(self):
        now = datetime(2026, 10, 8, 23, tzinfo=timezone.utc)
        slot = datetime(2026, 10, 9, 22, tzinfo=timezone.utc)
        report = analysis.daily_analysis([{"name": "A"}], [{slot: 1000}], [{slot: 700}],
                                        "Europe/Berlin", now, "revision", ["Test"])
        self.assertEqual(report["forecast_days"][0]["date"], "2026-10-09")
        self.assertIsNone(report["forecast_days"][0]["shaded_wh"])
        self.assertEqual(report["forecast_days"][1]["date"], "2026-10-10")
        self.assertEqual(report["forecast_days"][1]["shaded_wh"], 700)

    def test_total_dst_does_not_sum_independently_averaged_incomplete_arrays(self):
        first = datetime(2026, 10, 25, 0, tzinfo=timezone.utc)
        second = datetime(2026, 10, 25, 1, tzinfo=timezone.utc)
        report = analysis.daily_analysis([{"name": "A"}, {"name": "B"}],
            [{first: 100}, {second: 200}], [{first: 50}, {second: 100}],
            "Europe/Berlin", first, "revision", ["Test"])
        self.assertIsNone(report["total"]["hours"][2]["baseline_w"])
        self.assertIsNone(report["forecast_days"][0]["shaded_wh"])
        self.assertEqual(report["forecast_days"][0]["expected_hours"], 25)

    def test_full_dst_days_have_correct_energy_and_coverage(self):
        for start, length in ((datetime(2026, 3, 28, 23, tzinfo=timezone.utc), 23),
                              (datetime(2026, 10, 24, 22, tzinfo=timezone.utc), 25)):
            with self.subTest(length=length):
                values = {start + timedelta(hours=hour): 100 for hour in range(length)}
                report = analysis.daily_analysis([{"name": "A"}], [values], [values],
                    "Europe/Berlin", start + timedelta(hours=12), "revision", ["Test"])
                day = report["forecast_days"][0]
                self.assertEqual(day["covered_hours"], length)
                self.assertEqual(day["expected_hours"], length)
                self.assertEqual(day["shaded_wh"], length * 100)

    def test_independent_inverter_limits_never_increase_shadowed_power(self):
        ts = datetime(2026, 10, 9, 12, tzinfo=timezone.utc)
        surfaces = [{"inverter_id": "one"}, {"inverter_id": "one"}, {"inverter_id": "two"}]
        values = [{ts: 1000}, {ts: 1000}, {ts: 1000}]
        limited = analysis.apply_inverter_limits(values, surfaces, [
            {"id": "one", "limit_w": 800}, {"id": "two", "limit_w": 500},
        ])
        self.assertEqual([m[ts] for m in limited], [400, 400, 500])
        self.assertEqual(sum(m[ts] for m in limited), 1300)

    def test_24_hours_exact_baseline_actual_and_percent(self):
        start = datetime(2026, 10, 9, tzinfo=timezone.utc)
        base = {start.replace(hour=h): 1000 if 8 <= h < 18 else 0 for h in range(24)}
        shaded = {ts: v * 0.5 for ts, v in base.items()}
        report = analysis.daily_analysis([{"name": "Roof"}], [base], [shaded],
                                          "UTC", start, "revision", ["Test"])
        result = report["arrays"][0]
        self.assertEqual([h["hour"] for h in result["hours"]], list(range(24)))
        self.assertEqual(result["hours"][12]["baseline_w"], 1000)
        self.assertEqual(result["hours"][12]["shaded_w"], 500)
        self.assertEqual(result["hours"][12]["reduction_percent"], 50)
        self.assertEqual(result["hours"][0]["reduction_percent"], 0)
        self.assertEqual(result["baseline_wh"], 10000)
        self.assertEqual(result["shaded_wh"], 5000)
        self.assertEqual(result["reduction_percent"], 50)
        self.assertEqual(result["covered_hours"], 24)

    def test_day_uses_local_timezone_and_missing_data_stays_missing(self):
        now = datetime(2026, 10, 8, 23, 30, tzinfo=timezone.utc)
        slot = datetime(2026, 10, 8, 22, tzinfo=timezone.utc)
        report = analysis.daily_analysis([{"name": "Roof"}], [{slot: 100}], [{slot: 50}],
                                          "Europe/Berlin", now, "revision", ["Test"])
        self.assertEqual(report["date"], "2026-10-09")
        self.assertEqual(report["arrays"][0]["hours"][0]["baseline_w"], 100)
        self.assertIsNone(report["arrays"][0]["hours"][1]["baseline_w"])
        self.assertIsNone(report["arrays"][0]["hours"][1]["reduction_percent"])

    def test_spring_dst_gap_is_not_fabricated(self):
        slots = {datetime(2026, 3, 29, h, tzinfo=timezone.utc): 100 for h in range(22)}
        report = analysis.daily_analysis([{"name": "Roof"}], [slots], [slots],
                                          "Europe/Berlin", datetime(2026, 3, 29, 12, tzinfo=timezone.utc),
                                          "revision", ["Test"])
        self.assertEqual(len(report["arrays"][0]["hours"]), 24)
        self.assertIsNone(report["arrays"][0]["hours"][2]["baseline_w"])

    def test_fall_dst_hour_counts_energy_twice_and_averages_power(self):
        slots = {datetime(2026, 10, 25, 0, tzinfo=timezone.utc): 100,
                 datetime(2026, 10, 25, 1, tzinfo=timezone.utc): 300}
        report = analysis.daily_analysis([{"name": "Roof"}], [slots], [slots],
                                          "Europe/Berlin", datetime(2026, 10, 25, 12, tzinfo=timezone.utc),
                                          "revision", ["Test"])
        hour = report["arrays"][0]["hours"][2]
        self.assertEqual(hour["baseline_w"], 200)
        self.assertEqual(hour["baseline_wh"], 400)

    def test_grouping_and_weighting_same_for_baseline_and_shaded(self):
        ts = datetime(2026, 10, 9, 12, tzinfo=timezone.utc)
        combined = analysis.combine_arrays([
            ([{ts: 1000}, {ts: 2000}], 75),
            ([{ts: 2000}, {ts: 1000}], 25),
        ], 2)
        self.assertEqual([v[ts] for v in combined], [1250, 1750])
        grouped = analysis.group_arrays(combined, [0, 0], 1)
        self.assertEqual(grouped[0][ts], 3000)
        estimate = analysis.estimate_from_arrays(grouped, "UTC")
        self.assertEqual(estimate.watts[ts], 3000)
        self.assertEqual(sum(estimate.wh_days.values()), 3000)

    def test_zero_weights_and_missing_source_hours(self):
        a = datetime(2026, 10, 9, 12, tzinfo=timezone.utc)
        b = a.replace(hour=13)
        combined = analysis.combine_arrays([([{a: 100}], 0), ([{a: 300, b: 500}], 0)], 1)
        self.assertEqual(combined[0], {a: 200, b: 500})

    def test_grouped_arrays_and_site_never_turn_missing_surface_hours_into_partial_totals(self):
        a = datetime(2026, 10, 10, 10, tzinfo=timezone.utc)
        b = a.replace(hour=11)
        c = a.replace(hour=12)
        grouped = analysis.group_arrays([{c: 300, b: 200, a: 100}, {a: 400, c: 500}], [0, 0], 1)
        self.assertEqual(list(grouped[0]), [a, c])
        self.assertEqual(grouped[0], {a: 500, c: 800})
        estimate = analysis.estimate_from_arrays([{c: 300, b: 200, a: 100},
                                                 {a: 400, c: 500}], "UTC")
        self.assertEqual(list(estimate.wh_period), [a, c])
        self.assertEqual(estimate.wh_period, {a: 500, c: 800})
        self.assertEqual(sum(estimate.wh_days.values()), 1300)


class InverterTests(unittest.TestCase):
    def test_inverter_model_and_reference_validation(self):
        inverters = site.normalize_inverters([{
            "id": "one", "name": "Roof", "limit_w": 5000,
            "power_entity": "sensor.pv_power", "energy_entity": "sensor.pv_energy",
        }])
        arrays = site.normalize_arrays([array(inverter_id="one")])
        site.validate_inverter_assignments(arrays, inverters)
        with self.assertRaises(ValueError):
            site.validate_inverter_assignments(arrays, [])
        for invalid in (
            [{"id": "one", "name": "Roof", "limit_w": 0}],
            [{"id": "one", "name": "Roof", "limit_w": 1000, "power_entity": "switch.power"}],
            [{"id": "one", "name": "Roof", "limit_w": 1000}] * 2,
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                site.normalize_inverters(invalid)

    def test_settings_allow_clearing_legacy_cap_and_reject_unknown_or_fractional_days(self):
        self.assertIsNone(site.normalize_settings({"inverter_size": None})["inverter_size"])
        for invalid in ({"enable_self_optimization": True}, {"daily_yield_sensor_days": 2.5},
                        {"damping_morning": 1.1}, {"inverter_size": 0}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                site.normalize_settings(invalid)


if __name__ == "__main__":
    unittest.main()
