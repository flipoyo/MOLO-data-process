import json
import tempfile
import unittest
from pathlib import Path

from raw_to_processed.fixtures import make_calibration_records
from raw_to_processed.thermal_config import configure_thermal


class ThermalConfigurationWizard(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "lab").mkdir()
        self.lab_path = self.root / "lab" / "lab.json"
        self.lab_path.write_text(json.dumps({
            "schema": 1,
            "calibrations": {"P508": {
                "beta": 3834, "v_ref": 3.3, "intercept": 1.2,
                "dudh": -1.4, "dudt": 0.001, "t0_k": 298.15
            }},
            "thermal_calibrations": {},
            "sampling_points": {"Point034": {"calibration": "P508", "devices": []}}
        }), encoding="utf-8")
        self.calibration_path = self.root / "calibration" / "thermal" / "P508_gen1" / "RECORDS.csv"
        make_calibration_records(self.calibration_path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_wizard_writes_valid_registry_and_point_reference(self):
        answers = iter([
            "P508_gen1",
            "calibration/thermal/P508_gen1/RECORDS.csv",
            "records",
            "Value_Temperature_2",
            "Value_Temperature_3",
            "Value_Pressure",
            "TempBed",
            "",
            "Point034",
            "oui",
        ])
        result = configure_thermal(self.root, input_fn=lambda _prompt: next(answers), print_fn=lambda *_: None)
        data = json.loads(self.lab_path.read_text(encoding="utf-8"))
        self.assertEqual(result["generation"], "P508_gen1")
        self.assertEqual(data["thermal_calibrations"]["P508_gen1"]["columns"]["external"], "Value_Temperature_2")
        self.assertEqual(data["sampling_points"]["Point034"]["hardware_generation"], "P508_gen1")
        backups = list((self.root / "lab").glob("lab.json.bak-*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(json.loads(backups[0].read_text(encoding="utf-8"))["thermal_calibrations"], {})

    def test_wizard_does_not_save_when_user_cancels(self):
        answers = iter([
            "P508_gen1",
            "calibration/thermal/P508_gen1/RECORDS.csv",
            "records",
            "Value_Temperature_2",
            "Value_Temperature_3",
            "Value_Pressure",
            "TempBed",
            "",
            "Point034",
            "non",
        ])
        original = self.lab_path.read_text(encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "annulée"):
            configure_thermal(self.root, input_fn=lambda _prompt: next(answers), print_fn=lambda *_: None)
        self.assertEqual(self.lab_path.read_text(encoding="utf-8"), original)


if __name__ == "__main__":
    unittest.main()
