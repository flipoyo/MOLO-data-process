import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from raw_to_processed.datarepo import register_legacy_csv, run_point, verify_raw
from raw_to_processed.lab import VirtualLab
from raw_to_processed.readers import read_legacy_csv


class Point048LegacyCSV(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "data"
        self.raw = self.root / "raw_data" / "Point048" / "legacy_csv"
        self.raw.mkdir(parents=True)
        (self.raw / "pressure.csv").write_text(
            "#,dates,pressure_differential_V,temperature_stream_C\n"
            "1,14/04/2018 13:45:00,1.01682,13.778\n"
            "2,14/04/2018 14:00:00,1.04383,13.660\n", encoding="utf-8")
        (self.raw / "temperature.csv").write_text(
            "#,dates,temperature_depth_1_C,temperature_depth_2_C,temperature_depth_3_C,temperature_depth_4_C\n"
            "1,14/04/2018 13:45:00,13.124,12.467,12.022,11.821\n"
            "2,14/04/2018 14:00:00,13.081,12.388,11.910,11.686\n", encoding="utf-8")
        self.lab = VirtualLab({
            "schema": 1,
            "calibrations": {"P606_T001": {"beta": 3834.0, "v_ref": 3.3,
                "intercept": 1.27109693909151, "dudh": 1.01010102487901,
                "dudt": 0.00220946701100414, "t0_k": 298.15}},
            "sampling_points": {"Point048": {"calibration": "P606_T001", "devices": [],
                "legacy_csv": {"date_fmt": "%d/%m/%Y %H:%M:%S", "utc_offset_hours": None,
                               "source_timezone": "unknown_naive_preserved"}}}
        })

    def tearDown(self):
        self.tmp.cleanup()

    def test_reader_normalizes_without_reconverting_temperature(self):
        raw = read_legacy_csv(self.raw)
        self.assertEqual(list(raw.pressure.columns), ["Date", "Voltage", "Temp_Stream"])
        self.assertEqual(list(raw.temperature.columns), ["Date", "Temp1", "Temp2", "Temp3", "Temp4"])
        self.assertEqual(raw.temperature.loc[0, "Temp1"], 13.124)

    def test_register_run_and_manifest(self):
        files = register_legacy_csv(self.root, "Point048", self.lab)
        self.assertIn("legacy_csv/pressure.csv", files)
        self.assertEqual(verify_raw(self.root / "raw_data" / "Point048"), [])
        manifest, _ = run_point(self.root, "Point048", self.lab, source="legacy_csv")
        self.assertEqual(manifest["step"], "legacy_csv_to_ready")
        output = pd.read_csv(self.root / "processed_data" / "Point048" / "data" / "measures.csv")
        self.assertEqual(len(output), 2)
        self.assertEqual(output.loc[0, "Date"], "2018-04-14T13:45:00")
        self.assertIn("source_timezone", manifest["params"])

    def test_mismatched_timestamps_are_rejected(self):
        p = self.raw / "pressure.csv"
        p.write_text(p.read_text(encoding="utf-8").replace("14:00:00", "14:01:00"), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "horodatages"):
            read_legacy_csv(self.raw)


if __name__ == "__main__":
    unittest.main()
