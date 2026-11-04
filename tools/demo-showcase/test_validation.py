"""Portable, standard-library validation checks; no credentials or network calls."""
import copy
import unittest
from types import SimpleNamespace

import update_demo


def plan(temperature=23.5, brightness=1800, occupied=True, distance=60):
    return update_demo.prepare(SimpleNamespace(action="telemetry", update_id="validation01",
        exhibit_id="demo-test-clock", temperature=temperature, brightness_raw=brightness,
        occupied=occupied, distance_cm=distance))


class NoRequests:
    def request(self, *args, **kwargs):
        raise AssertionError("Invalid plans must be rejected before any API request.")


class ValidationTests(unittest.TestCase):
    def test_generated_telemetry_plan_is_valid(self):
        update_demo.validate_plan(plan())

    def test_invalid_second_record_types_stop_before_any_request(self):
        cases = [(plan(), "occupied", 1), (plan(occupied=False), "occupied", 0),
                 (plan(temperature=1), "temperature_c", True),
                 (plan(temperature=0), "temperature_c", False),
                 (plan(brightness=0), "brightness_percent", False),
                 (plan(distance=1), "distance_cm", True)]
        for original, field, invalid in cases:
            with self.subTest(field=field, invalid=invalid):
                changed = copy.deepcopy(original)
                changed["operations"][1]["payload"][field] = invalid
                with self.assertRaises(ValueError):
                    update_demo.apply(changed, producer=NoRequests())

    def test_numeric_second_record_ranges_stop_before_any_request(self):
        for field, invalid in (("temperature_c", 126), ("brightness_percent", 101), ("distance_cm", -1)):
            with self.subTest(field=field):
                changed = plan()
                changed["operations"][1]["payload"][field] = invalid
                with self.assertRaises(ValueError):
                    update_demo.apply(changed, producer=NoRequests())


if __name__ == "__main__":
    unittest.main()
