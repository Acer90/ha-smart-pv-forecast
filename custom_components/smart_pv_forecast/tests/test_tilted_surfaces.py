import math
import unittest
from datetime import date

from support import load

site = load("site")


def point(x, y, height=None):
    result = {"latitude": math.degrees(y / 6371000), "longitude": math.degrees(x / 6371000)}
    if height is not None:
        result["height"] = height
    return result


def arrays(tilt=30, azimuth=180, vertical_height=None):
    return site.normalize_arrays([{
        "peak": 1000, "tilt": tilt, "azimuth": azimuth, "height": 2,
        "areas": [{"polygon": [point(0, 0), point(10, 0), point(10, 6), point(0, 6)],
                   "vertical_height": vertical_height}],
    }])


class TiltedSurfaceTests(unittest.TestCase):
    def test_upper_height_uses_footprint_depth_along_azimuth(self):
        for azimuth, span in ((0, 6), (180, 6), (90, 10), (270, 10)):
            profile = site.surface_profile(arrays(azimuth=azimuth)[0]["areas"][0])
            self.assertAlmostEqual(profile["span"], span, places=6)
            self.assertAlmostEqual(profile["upper_height"], 2 + span * math.tan(math.radians(30)), places=6)
        self.assertEqual(site.surface_profile(arrays(tilt=0)[0]["areas"][0])["upper_height"], 2)

    def test_oblique_direction_uses_full_projected_extent(self):
        profile = site.surface_profile(arrays(azimuth=45)[0]["areas"][0])
        self.assertAlmostEqual(profile["span"], (10 + 6) / math.sqrt(2), places=6)

    def test_samples_rise_away_from_facing_direction(self):
        for azimuth in (0, 180):
            value = arrays(azimuth=azimuth)[0]
            samples = site.surface_samples(value, 0, 0)
            for x, y, height, weight in samples:
                expected = 2 + (y if azimuth == 180 else 6 - y) * math.tan(math.radians(30))
                self.assertAlmostEqual(height, expected, places=6)
                self.assertGreater(weight, 0)
            self.assertAlmostEqual(sum(sample[3] for sample in samples), 1)

    def test_varying_height_changes_actual_object_shading(self):
        objects = site.normalize_objects([{"height": 9, "solid": True, "transmission": 0,
                                          "line": [point(-10, -1, 9), point(20, -1, 9)]}])
        flat = site.ObjectShading(arrays(tilt=0), objects, 0, 0)
        tilted = site.ObjectShading(arrays(tilt=30), objects, 0, 0)
        self.assertAlmostEqual(flat.at_sun(0, 180, 45, date(2026, 6, 21)), 0)
        self.assertAlmostEqual(tilted.at_sun(0, 180, 45, date(2026, 6, 21)), 1 / 3)

    def test_vertical_extent_and_vertical_plane_samples(self):
        value = arrays(tilt=90, vertical_height=3)[0]
        profile = site.surface_profile(value["areas"][0])
        self.assertEqual(profile["upper_height"], 5)
        self.assertFalse(site.geometry_required([value]))
        samples = site.surface_samples(value, 0, 0)
        for x, y, height, weight in samples:
            self.assertAlmostEqual(y, 3, places=6)
            self.assertTrue(2 < height < 5)
        self.assertAlmostEqual(sum(s[2] * s[3] for s in samples), 3.5)

    def test_incomplete_vertical_geometry_stays_editable_but_cannot_be_calculated(self):
        value = arrays(tilt=90)[0]
        self.assertTrue(site.geometry_required([value]))
        self.assertIsNone(site.surface_profile(value["areas"][0])["upper_height"])
        with self.assertRaisesRegex(ValueError, "vertical extent"):
            site.surface_samples(value, 0, 0)

    def test_unrealistic_derived_height_requires_configuration(self):
        value = arrays(tilt=89.9)
        self.assertTrue(site.geometry_required(value))
        self.assertGreater(site.surface_profile(value[0]["areas"][0])["upper_height"], 1000)

    def test_invalid_vertical_extent_is_not_defaulted(self):
        for height in (0, -1, float("nan"), True):
            with self.assertRaises(ValueError):
                arrays(tilt=90, vertical_height=height)


if __name__ == "__main__":
    unittest.main()
