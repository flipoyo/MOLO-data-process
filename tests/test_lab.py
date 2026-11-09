import copy
import json
import tempfile
import unittest
from pathlib import Path

from raw_to_processed.lab import VirtualLab
from raw_to_processed.virtual_lab import DEFAULT_CALIBRATION as CAL

BASE = {
    "schema": 1,
    "calibrations": {"C1": CAL.to_dict()},
    "sampling_points": {
        "PointA": {"calibration": "C1", "devices": ["DEV-A1", "DEV-A2"], "params": {"dedupe": False},
                   "v1": {"utc_offset_hours": 1.0}},
        "PointB": {"calibration": "C1", "devices": ["DEV-B1"]},
    },
}


class Lab(unittest.TestCase):
    def test_routing_and_lookups(self):
        lab = VirtualLab(BASE)
        self.assertEqual(lab.device_point("DEV-A2"), "PointA")
        self.assertEqual(lab.device_point("DEV-B1"), "PointB")
        self.assertIsNone(lab.device_point("INCONNU"))
        self.assertEqual(lab.points(), ["PointA", "PointB"])
        self.assertEqual(lab.devices("PointA"), ["DEV-A1", "DEV-A2"])
        self.assertEqual(lab.calibration("PointA"), CAL)

    def test_params_merge_common_then_source_specific(self):
        lab = VirtualLab(BASE)
        self.assertEqual(lab.params("PointA", "v1"), {"dedupe": False, "utc_offset_hours": 1.0})
        self.assertEqual(lab.params("PointA", "v2"), {"dedupe": False})
        self.assertEqual(lab.params("PointB", "v1"), {})

    def test_a_device_cannot_belong_to_two_points(self):
        bad = copy.deepcopy(BASE)
        bad["sampling_points"]["PointB"]["devices"].append("DEV-A1")
        with self.assertRaisesRegex(ValueError, "DEV-A1"):
            VirtualLab(bad)

    def test_unknown_calibration_is_refused(self):
        bad = copy.deepcopy(BASE)
        bad["sampling_points"]["PointB"]["calibration"] = "NOPE"
        with self.assertRaisesRegex(ValueError, "calibration inconnue"):
            VirtualLab(bad)

    def test_wrong_schema_is_refused(self):
        with self.assertRaises(ValueError):
            VirtualLab({**BASE, "schema": 2})

    def test_save_load_roundtrip_and_digest_follows_content(self):
        with tempfile.TemporaryDirectory() as t:
            path = Path(t) / "lab" / "lab.json"
            VirtualLab(BASE).save(path)
            loaded = VirtualLab.load(path)
            self.assertEqual(loaded.data, BASE)
            self.assertEqual(loaded.digest(), VirtualLab(BASE).digest())
            changed = copy.deepcopy(BASE)
            changed["calibrations"]["C1"]["beta"] += 1
            self.assertNotEqual(VirtualLab(changed).digest(), loaded.digest())
            self.assertTrue(json.loads(path.read_text(encoding="utf-8"))["schema"] == 1)


if __name__ == "__main__":
    unittest.main()
