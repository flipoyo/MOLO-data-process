import unittest

from raw_to_processed.lab import VirtualLab


CALIBRATION = {
    "beta": 3834.0,
    "v_ref": 3.3,
    "intercept": 1.2,
    "dudh": -1.4,
    "dudt": 0.001,
    "t0_k": 298.15,
}
THERMAL = {
    "table": "calibration/thermal/P508_generation_1/RECORDS.csv",
    "columns": {
        "external": "Value_Temperature_2",
        "internal": "Value_Temperature_3",
        "pressure": "Value_Pressure",
    },
    "external_series": "TempBed",
}


def make_lab():
    return VirtualLab({
        "schema": 1,
        "calibrations": {"P508": CALIBRATION},
        "thermal_calibrations": {"P508_generation_1": THERMAL},
        "sampling_points": {
            "Point034": {
                "calibration": "P508",
                "hardware_generation": "P508_generation_1",
                "devices": [],
            },
            "Point036": {
                "calibration": "P508",
                "thermal_calibration": "P508_generation_1",
                "devices": [],
            },
            "Point048": {"calibration": "P508", "devices": []},
        },
    })


class ThermalCalibrationRegistry(unittest.TestCase):
    def test_hardware_generation_selects_its_calibration(self):
        self.assertEqual(make_lab().thermal("Point034"), THERMAL)

    def test_explicit_calibration_reference_takes_precedence(self):
        lab = make_lab()
        self.assertEqual(lab.thermal("Point036"), THERMAL)

    def test_point_without_thermal_calibration_remains_unmodified(self):
        self.assertIsNone(make_lab().thermal("Point048"))

    def test_legacy_inline_calibration_remains_supported(self):
        data = make_lab().data
        data["sampling_points"]["Point048"]["thermal_calibration"] = THERMAL
        self.assertEqual(VirtualLab(data).thermal("Point048"), THERMAL)

    def test_unknown_explicit_reference_is_rejected(self):
        data = make_lab().data
        data["sampling_points"]["Point048"]["thermal_calibration"] = "UNKNOWN"
        with self.assertRaisesRegex(ValueError, "inconnue"):
            VirtualLab(data)


if __name__ == "__main__":
    unittest.main()
