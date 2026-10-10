import math
import unittest
from datetime import date, datetime, timedelta, timezone

from support import load

site = load("site")


def obstacle(**changes):
    return site.normalize_objects([{
        "name": "Wall", "height": 12, "solid": True, "transmission": 0,
        "line": [
            {"latitude": 51, "longitude": 6.9999, "height": 12},
            {"latitude": 51, "longitude": 7.0001, "height": 12},
        ],
        **changes,
    }])[0]


class ShadowProjectionTests(unittest.TestCase):
    def project(self, obj, height=0, elevation=45, when=date(2026, 6, 21)):
        return site.project_shadows([obj], 51, 7, height, 0, elevation, when)

    def test_shadow_direction_length_and_pv_height(self):
        for height, expected in ((0, 12), (6, 6), (11, 1)):
            shadows, truncated = self.project(obstacle(), height=height)
            latitudes = [p["latitude"] for p in shadows[0]["polygons"][0]]
            length = math.radians(51 - min(latitudes)) * 6371000
            self.assertAlmostEqual(length, expected, places=6)
            self.assertAlmostEqual(max(latitudes), 51)
            self.assertEqual(shadows[0]["reduction"], 1)
            self.assertFalse(truncated)
        self.assertEqual(self.project(obstacle(), height=12)[0], [])

    def test_contour_crossing_plane_is_clipped_at_interpolated_height(self):
        obj = obstacle(line=[
            {"latitude": 51, "longitude": 6.9999, "height": 2},
            {"latitude": 51, "longitude": 7.0001, "height": 10},
        ])
        shadows, _ = self.project(obj, height=6)
        polygon = shadows[0]["polygons"][0]
        self.assertGreaterEqual(min(p["longitude"] for p in polygon), 7 - 1e-10)
        length = math.radians(51 - min(p["latitude"] for p in polygon)) * 6371000
        self.assertAlmostEqual(length, 4, places=6)

    def test_night_and_transparent_objects_have_no_shadows(self):
        self.assertEqual(self.project(obstacle(), elevation=0)[0], [])
        self.assertEqual(self.project(obstacle(), elevation=-10)[0], [])
        self.assertEqual(self.project(obstacle(transmission=1))[0], [])

    def test_seasonal_and_manual_reductions(self):
        tree = obstacle(solid=False, reduction_leaf_on=0.9, reduction_leaf_off=0.5)
        self.assertAlmostEqual(self.project(tree)[0][0]["reduction"], 0.9)
        self.assertAlmostEqual(self.project(tree, when=date(2026, 1, 1))[0][0]["reduction"], 0.5)
        tree["foliage_mode"] = "leaf_off"
        self.assertAlmostEqual(self.project(tree)[0][0]["reduction"], 0.5)

    def test_very_long_shadows_report_visible_truncation(self):
        shadows, truncated = self.project(obstacle(), elevation=0.01)
        self.assertTrue(truncated)
        polygon = shadows[0]["polygons"][0]
        length = math.radians(51 - min(p["latitude"] for p in polygon)) * 6371000
        self.assertAlmostEqual(length, 10000, places=5)

    def test_long_varying_contour_is_clipped_without_changing_nearby_shadow(self):
        obj = obstacle(line=[
            {"latitude": 51, "longitude": 6.9999, "height": 1000},
            {"latitude": 51, "longitude": 7.0001, "height": 1},
        ])
        shadows, truncated = self.project(obj, elevation=0.01)
        self.assertTrue(truncated)
        polygon = shadows[0]["polygons"][0]
        self.assertEqual(len(polygon), 5)
        self.assertAlmostEqual(
            math.radians(51 - min(p["latitude"] for p in polygon)) * 6371000,
            10000, places=5,
        )

    def test_preview_footprint_matches_forecast_ray_intersections(self):
        obj = obstacle(line=[
            {"latitude": 51, "longitude": 6.9999, "height": 2},
            {"latitude": 51, "longitude": 7.0001, "height": 10},
        ])
        line = [(*site._local_point(51, 7, p), p["height"]) for p in obj["line"]]
        for azimuth in (0, 40, 180, 280):
            shadows, _ = site.project_shadows([obj], 51, 7, 6, azimuth, 35, date(2026, 6, 21))
            polygons = [[site._local_point(51, 7, p) for p in polygon]
                        for polygon in shadows[0]["polygons"]]
            for x in (-8.3, -3.3, 1.7, 6.7):
                for y in (-12.3, -7.3, -2.3, 2.7, 7.7, 12.7):
                    projected = any(site._contains_origin([(px - x, py - y) for px, py in polygon])
                                    for polygon in polygons)
                    self.assertEqual(projected, site.line_blocks(line, azimuth, 35, (x, y, 6, 1)))

    def test_preview_uses_real_solar_position_and_never_weather(self):
        position = {"latitude": 51, "longitude": 7}
        noon = site.shadow_preview([obstacle()], position, 6,
                                   datetime(2026, 6, 21, 11, tzinfo=timezone.utc))
        self.assertGreater(noon["sun"]["elevation"], 50)
        self.assertTrue(90 < noon["sun"]["azimuth"] < 270)
        self.assertTrue(noon["shadows"])
        night = site.shadow_preview([obstacle()], position, 6,
                                    datetime(2026, 6, 21, 0, tzinfo=timezone.utc))
        self.assertLess(night["sun"]["elevation"], 0)
        self.assertEqual(night["shadows"], [])

    def test_invalid_height_position_geometry_and_naive_time_rejected(self):
        when = datetime(2026, 6, 21, 12, tzinfo=timezone.utc)
        for position, height, objects, instant in (
            ({"latitude": 51, "longitude": 7}, -1, [], when),
            ({"latitude": float("nan"), "longitude": 7}, 0, [], when),
            ({"latitude": 51, "longitude": 7}, 0, [{"line": []}], when),
            ({"latitude": 51, "longitude": 7}, 0, [], when.replace(tzinfo=None)),
        ):
            with self.assertRaises(ValueError):
                site.shadow_preview(objects, position, height, instant)


class PreviewClockTests(unittest.TestCase):
    def test_live_time_uses_home_assistant_timezone(self):
        now = datetime(2026, 6, 21, 12, tzinfo=timezone.utc)
        when = site.preview_time(None, "Europe/Berlin", now)
        self.assertEqual(when.hour, 14)
        self.assertEqual(when.utcoffset(), timedelta(hours=2))
        self.assertEqual(when.astimezone(timezone.utc), now)

    def test_picker_is_local_not_browser_time(self):
        when = site.preview_time("2026-06-21T12:30", "Europe/Berlin",
                                 datetime.now(timezone.utc))
        self.assertEqual(when.hour, 12)
        self.assertEqual(when.minute, 30)
        self.assertEqual(when.astimezone(timezone.utc).hour, 10)

    def test_missing_dst_hour_and_invalid_dates_rejected(self):
        for value in ("2026-03-29T02:30", "", "2026-02-30T12:00", "1999-06-21T12:00"):
            with self.assertRaises(ValueError):
                site.preview_time(value, "Europe/Berlin", datetime.now(timezone.utc))

    def test_repeated_hour_defaults_to_first_and_explicit_offset_disambiguates(self):
        now = datetime.now(timezone.utc)
        first = site.preview_time("2026-10-25T02:30", "Europe/Berlin", now)
        second = site.preview_time("2026-10-25T02:30+01:00", "Europe/Berlin", now)
        self.assertEqual(first.utcoffset(), timedelta(hours=2))
        self.assertEqual(second.utcoffset(), timedelta(hours=1))
        self.assertEqual(second.fold, 1)


if __name__ == "__main__":
    unittest.main()
