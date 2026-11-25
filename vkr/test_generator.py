import itertools
import math
import unittest

from app import SITE
from generator import Rect, generate


class GeneratorTests(unittest.TestCase):
    def test_defence_scenarios(self):
        for target, seats, expected, reason in [
            (150, 60, 150, "target"), (300, 60, 300, "target"),
            (300, 20, 200, "school"), (300, 0, 0, "school"),
            (0, 0, 0, "target"), (11, 2, 11, "target")
        ]:
            with self.subTest(target=target, seats=seats):
                data = generate(SITE, target, seats, 42)
                self.assertEqual(data["reason"], reason)
                self.assertEqual(data["population"], expected)
                self.assertEqual(data["deficit"], target - expected)
                self.assertEqual(data["used_seats"], math.ceil(expected / 10))

    def test_reproducibility(self):
        first = generate(SITE, 300, 60, 42)
        self.assertEqual(first, generate(SITE, 300, 60, 42))
        self.assertNotEqual(first["buildings"], generate(SITE, 300, 60, 43)["buildings"])

    def test_geometry_and_population_when_land_is_exhausted(self):
        for seed in (0, 1, 42, 43, 913):
            data = generate(SITE, 100_000, 100_000, seed)
            self.assertEqual(data["reason"], "land")
            self.assertGreater(data["rejected"]["geometry"], 0)
            self.assertGreater(data["rejected"]["radius"], 0)
            self.assertEqual(sum(b["residents"] for b in data["buildings"]), data["population"])
            homes = []
            for b in data["buildings"]:
                x, y, w, h = (b[k] for k in ("x", "y", "w", "h"))
                homes.append((x, y, x + w, y + h))
                self.assertTrue(0 <= x < x+w <= SITE["width"] and 0 <= y < y+h <= SITE["height"])
                self.assertLessEqual(math.dist((x+w/2, y+h/2), SITE["school_point"]), SITE["radius"])
                for road in SITE["roads"]:
                    self.assertTrue(y+h < road["y"]-road["width"]/2 or y > road["y"]+road["width"]/2)
                for zone in (SITE["park"], SITE["school"]):
                    self.assertTrue(x+w < zone["x"] or x > zone["x"]+zone["w"]
                                    or y+h < zone["y"] or y > zone["y"]+zone["h"])
                self.assertEqual(b["capacity"], math.floor(w*h*b["floors"]*0.75/30))
                self.assertTrue(0 < b["residents"] <= b["capacity"])
            for a, b in itertools.combinations(homes, 2):
                dx, dy = max(a[0]-b[2], b[0]-a[2], 0), max(a[1]-b[3], b[1]-a[3], 0)
                self.assertGreaterEqual(math.hypot(dx, dy), 6)

    def test_rectangle_edges_and_diagonal_gap(self):
        base = Rect(0, 0, 10, 10)
        self.assertEqual(base.distance(Rect(10, 8, 5, 5)), 0)
        self.assertEqual(base.distance(Rect(13, 14, 1, 1)), 5)
        self.assertTrue(base.contains(Rect(9, 9, 1, 1)))
        self.assertFalse(base.contains(Rect(9, 9, 2, 1)))


if __name__ == "__main__":
    unittest.main()
