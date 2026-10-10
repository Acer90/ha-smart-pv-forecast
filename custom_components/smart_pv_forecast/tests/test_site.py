import json
import math
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from support import load

site = load("site")


def point(east, north):
    return {
        "latitude": math.degrees(north / site.EARTH_RADIUS),
        "longitude": math.degrees(east / site.EARTH_RADIUS),
    }


def array(height=0, **values):
    return {"peak": 1000, "azimuth": 180, "tilt": 30, "height": height, **values}


def obstacle(height=10, transmission=0.2):
    return {"height": height, "transmission": transmission,
            "polygon": [point(-2, 10), point(2, 10), point(2, 14), point(-2, 14)]}


class SiteTests(unittest.TestCase):
    def shading(self, arrays=None, objects=None):
        return site.ObjectShading(
            site.normalize_arrays(arrays or [array()]),
            site.normalize_objects(objects or [obstacle()]), 0, 0,
        )

    def test_legacy_json_and_defaults(self):
        arrays = site.normalize_arrays(json.dumps([{"peak": 3000, "azimuth": 360, "tilt": 20}]))
        self.assertEqual(arrays[0]["height"], 0)
        self.assertEqual(arrays[0]["name"], "PV 1")
        self.assertEqual(arrays[0]["mounting_type"], "roof_parallel")
        self.assertEqual(arrays[0]["azimuth"], 360)
        self.assertEqual(site.normalize_objects("[]"), [])

    def test_mounting_type_and_tilt_are_independent(self):
        for mounting in site.MOUNTING_TYPES:
            result = site.normalize_arrays([array(mounting_type=mounting, tilt=17)])[0]
            self.assertEqual(result["mounting_type"], mounting)
            self.assertEqual(result["tilt"], 17)
        with self.assertRaises(ValueError):
            site.normalize_arrays([array(mounting_type="invalid")])

    def test_north_obstacle_low_sun_blocks_high_sun_clears(self):
        shading = self.shading()
        self.assertEqual(shading.at_sun(0, 0, 30), 0.2)
        self.assertEqual(shading.at_sun(0, 0, 60), 1)
        self.assertEqual(shading.at_sun(0, 180, 30), 1)
        self.assertEqual(shading.at_sun(0, 90, 30), 1)
        self.assertEqual(shading.at_sun(0, 359, 30), 0.2)

    def test_panel_height_changes_shadow(self):
        shading = self.shading(arrays=[array(0), array(8), array(10), array(12)])
        self.assertEqual([shading.at_sun(i, 0, 30) for i in range(4)], [0.2, 1, 1, 1])

    def test_array_position_changes_shadow(self):
        shading = self.shading(arrays=[array(), array(**point(50, 0))])
        self.assertEqual(shading.at_sun(0, 0, 30), 0.2)
        self.assertEqual(shading.at_sun(1, 0, 30), 1)

    def test_overlapping_objects_do_not_multiply_diffuse_loss(self):
        shading = self.shading(objects=[obstacle(transmission=0.5), obstacle(transmission=0.2)])
        self.assertEqual(shading.at_sun(0, 0, 30), 0.2)

    def test_full_opacity_is_not_clamped_to_adaptive_minimum(self):
        self.assertEqual(self.shading(objects=[obstacle(transmission=0)]).at_sun(0, 0, 30), 0)

    def test_inside_object_and_ray_edges(self):
        polygon = [(-1, -1), (1, -1), (1, 1), (-1, 1)]
        self.assertTrue(site.ray_hits(polygon, 180, 0.1))
        self.assertTrue(site.ray_hits([(0, 10), (2, 10), (2, 14), (0, 14)], 0, 12))
        self.assertFalse(site.ray_hits([(-2, 10), (2, 10), (2, 14), (-2, 14)], 0, 9.99))

    def test_partial_hour_sampling_and_cache(self):
        shading = self.shading()
        hour = datetime(2026, 6, 21, 12, tzinfo=timezone.utc)
        with patch("astral.sun.azimuth", return_value=0), patch(
            "astral.sun.elevation", side_effect=[30, 30, 30, 60, 60, 60]
        ) as elevation:
            self.assertAlmostEqual(shading.factor(0, hour), 0.6)
            self.assertAlmostEqual(shading.factor(0, hour), 0.6)
            self.assertEqual(elevation.call_count, 6)

    def test_no_objects_preserve_behavior(self):
        shading = site.ObjectShading(site.normalize_arrays([array()]), [], 0, 0)
        self.assertEqual(shading.factor(0, datetime.now(timezone.utc)), 1)

    def test_invalid_arrays_are_explicit_errors(self):
        for invalid in ([], {}, "broken", [array(peak=-1)], [array(peak=float("nan"))],
                        [array(tilt=91)], [array(azimuth=361)], [array(height=-1)],
                        [array(latitude=51)], [array(peak=True)]):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                site.normalize_arrays(invalid)

    def test_invalid_polygons_and_ranges(self):
        bad_polygons = [
            [point(0, 0), point(1, 1)],
            [point(0, 0), point(1, 1), point(2, 2)],
            [point(0, 0), point(1, 1), point(1, 1)],
            [point(0, 0), point(4, 4), point(0, 4), point(4, 0)],
            [point(0, 0), point(4, 0), point(2, 0), point(4, 4), point(0, 4)],
        ]
        for polygon in bad_polygons:
            with self.subTest(polygon=polygon), self.assertRaises(ValueError):
                site.normalize_objects([{"polygon": polygon, "height": 5}])
        for key, value in (("height", -1), ("height", float("inf")), ("transmission", 1.1)):
            with self.subTest(key=key), self.assertRaises(ValueError):
                site.normalize_objects([{**obstacle(), key: value}])

    def test_closed_polygon_normalized_and_reversed_equivalent(self):
        obj = obstacle()
        obj["polygon"].append(obj["polygon"][0])
        normalized = site.normalize_objects([obj])[0]
        self.assertEqual(len(normalized["polygon"]), 4)
        reversed_obj = {**normalized, "polygon": list(reversed(normalized["polygon"]))}
        self.assertEqual(self.shading(objects=[reversed_obj]).at_sun(0, 0, 30), 0.2)

    def test_antimeridian_local_projection(self):
        x, _ = site._local_point(0, 179.999, {"latitude": 0, "longitude": -179.999})
        self.assertAlmostEqual(x, 222.38985, places=3)


if __name__ == "__main__":
    unittest.main()
