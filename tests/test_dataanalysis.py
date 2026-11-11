import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from raw_to_processed.canonical import file_digest
from raw_to_processed.dataanalysis import classify, declared_offset_hours, import_dataanalysis, parse_geometry, point_name
from raw_to_processed.datarepo import RawIntegrityError, load_lab, raw_point_dir, run_all, run_point, verify_raw
from raw_to_processed.fixtures import build_dataanalysis_mini
from raw_to_processed.validate import compare_with_reference, parse_reference_dates

ROOT = Path(__file__).resolve().parents[1]


class Helpers(unittest.TestCase):
    def test_point_names_are_zero_padded(self):
        self.assertEqual(point_name("point7_27_06_16"), "Point007")
        self.assertEqual(point_name("point048_14_04_18"), "Point048")
        self.assertIsNone(point_name("configuration"))

    def test_declared_offset(self):
        self.assertEqual(declared_offset_hours('#,"Date Heure, GMT+02:00"'), 2.0)
        self.assertEqual(declared_offset_hours('"Date Heure, GMT-03:30"'), -3.5)
        self.assertIsNone(declared_offset_hours("#,dates,pressure_differential_V"))

    def test_geometry_with_an_empty_river_bed_depth(self):
        with tempfile.TemporaryDirectory() as t:
            path = Path(t) / "geometrie.txt"
            path.write_text("#pressure_sensor\np606\n#depth river bed - river tube [cm]\n\n#temperature_depths[cm]\n10;20;30;40", encoding="utf-8")
            self.assertEqual(parse_geometry(path), {"pressure_sensor": "p606", "river_bed_depth_cm": None,
                                                    "temperature_depths_cm": [10, 20, 30, 40]})

    def test_classify_uses_the_header_not_the_file_name(self):
        with tempfile.TemporaryDirectory() as t:
            a, b = Path(t) / "zzz.csv", Path(t) / "aaa.csv"
            a.write_text("#,dates,pressure_differential_V,temperature_stream_C\n", encoding="utf-8")
            b.write_text("#,dates,t1,t2,t3,t4\n", encoding="utf-8")
            self.assertEqual(classify([b, a]), (a, b))
            with self.assertRaisesRegex(ValueError, "ambigus"):
                classify([a, a])


class Import(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        build_dataanalysis_mini(self.tmp / "src")
        self.root = self.tmp / "data"
        self.report = import_dataanalysis(self.tmp / "src", self.root)
        self.lab = load_lab(self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def test_report_and_unimportable_point(self):
        self.assertEqual(self.report["Point007"]["status"], "importé")
        self.assertEqual(self.report["Point007"]["decalage_h"], 1.0)
        self.assertEqual(self.report["Point007"]["horodatages"], "intersection")
        self.assertEqual(self.report["Point008"]["horodatages"], "strict")
        self.assertIsNone(self.report["Point008"]["decalage_h"])
        self.assertEqual(self.report["Point009"]["status"], "ignoré")
        self.assertIn("P999", self.report["Point009"]["raison"])
        self.assertFalse((self.root / "raw_data" / "Point009").exists())

    def test_raw_files_are_byte_identical_copies(self):
        src = self.tmp / "src" / "raw_data"
        self.assertEqual(file_digest(raw_point_dir(self.root, "Point007") / "legacy_csv" / "pressure.csv"),
                         file_digest(src / "point7_27_06_16" / "p7.csv"))
        self.assertEqual(file_digest(raw_point_dir(self.root, "Point008") / "legacy_csv" / "temperature.csv"),
                         file_digest(src / "point8_14_04_18" / "t8.csv"))
        self.assertEqual(verify_raw(raw_point_dir(self.root, "Point008")), [])
        meta = json.loads((raw_point_dir(self.root, "Point008") / "meta" / "source_files.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["pressure"], "point8_14_04_18/p8.csv")

    def test_hardware_files_and_lab_calibrations(self):
        hw = self.root / "lab" / "hardware"
        self.assertTrue((hw / "pressure_sensors" / "P606.csv").is_file())
        self.assertTrue((hw / "shafts" / "S001.csv").is_file())
        self.assertAlmostEqual(self.lab.calibration("Point007").dudh, 1.01010102487901)
        self.assertAlmostEqual(self.lab.calibration("Point008").dudt, 0.0040301365342419)   # fichier P506 séparé par « ; »
        spec = self.lab.data["sampling_points"]["Point007"]
        self.assertEqual(spec["hardware"]["sha256"], file_digest(hw / "pressure_sensors" / "P606.csv"))
        self.assertEqual(spec["geometry"]["temperature_depths_cm"], [10, 20, 30, 40])
        self.assertEqual(spec["legacy_csv"]["source_timezone"], "declared_by_logger_header_GMT+1")
        self.assertEqual(self.lab.data["sampling_points"]["Point008"]["legacy_csv"]["source_timezone"], "unknown_naive_preserved")

    def test_sensor_override(self):
        other = self.tmp / "data2"
        report = import_dataanalysis(self.tmp / "src", other, sensors={"Point008": "P606"})
        self.assertEqual(report["Point008"]["capteur"], "P606")
        self.assertAlmostEqual(load_lab(other).calibration("Point008").dudh, 1.01010102487901)

    def test_run_all_converts_both_points_with_their_own_conventions(self):
        results = run_all(self.root)
        self.assertEqual(set(results), {"Point007", "Point008"})
        p7 = pd.read_csv(self.root / "processed_data/Point007/data/measures.csv")
        p8 = pd.read_csv(self.root / "processed_data/Point008/data/measures.csv")
        self.assertEqual(len(p7), 3)                                               # intersection des deux fichiers
        self.assertEqual(p7["Date"].iloc[0], "2016-06-27T11:00:00Z")               # 12:00 local, GMT+01:00 -> 11:00 UTC
        self.assertEqual(p8["Date"].iloc[0], "2018-04-14T13:45:00")                # fuseau inconnu : heure conservée, sans Z
        self.assertEqual(results["Point007"]["params"]["utc_offset_hours"], 1.0)
        self.assertIsNone(results["Point008"]["params"]["utc_offset_hours"])

    def test_tampering_with_imported_raw_blocks_the_run(self):
        (raw_point_dir(self.root, "Point008") / "legacy_csv" / "pressure.csv").write_text("x\n", encoding="utf-8")
        with self.assertRaises(RawIntegrityError):
            run_point(self.root, "Point008")

    def test_reimport_is_idempotent_and_never_overwrites(self):
        before = file_digest(raw_point_dir(self.root, "Point008") / "legacy_csv" / "pressure.csv")
        import_dataanalysis(self.tmp / "src", self.root)
        self.assertEqual(file_digest(raw_point_dir(self.root, "Point008") / "legacy_csv" / "pressure.csv"), before)
        (self.tmp / "src/raw_data/point8_14_04_18/p8.csv").write_text("#,dates,pressure_differential_V,temperature_stream_C\n"
                                                                      "1,14/04/2018 13:45:00,9,9\n", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            import_dataanalysis(self.tmp / "src", self.root)

    def test_validation_against_the_historical_reference(self):
        run_all(self.root)
        result = compare_with_reference(self.root, "Point008")
        self.assertEqual(result["status"], "conforme")
        self.assertEqual(result["couverture"], 1.0)
        self.assertEqual(compare_with_reference(self.root, "Point007")["status"], "pas de référence")

    def test_validation_detects_a_wrong_calibration(self):
        other = self.tmp / "wrong"
        import_dataanalysis(self.tmp / "src", other, sensors={"Point008": "P606"})      # mauvais capteur
        run_all(other)
        result = compare_with_reference(other, "Point008")
        self.assertEqual(result["status"], "écarts")
        self.assertEqual(result["variables"]["Pressure"]["niveau"], "écart")
        self.assertEqual(result["variables"]["Temp1"]["niveau"], "exact")

    def test_validation_tolerates_a_small_calibration_difference(self):
        run_all(self.root)
        ref = self.root / "reference_data/Point008/pression_cleaned.csv"
        lines = ref.read_text(encoding="utf-8").splitlines()
        head, row = lines[1].rsplit(",", 1)
        lines[1] = f"{head},{float(row) + 5e-5!r}"                                     # 5e-5 d'écart : « proche »
        ref.write_text("\n".join(lines) + "\n", encoding="utf-8")
        result = compare_with_reference(self.root, "Point008")
        self.assertEqual(result["status"], "proche")
        self.assertEqual(result["variables"]["Pressure"]["niveau"], "proche")


class ReferenceDates(unittest.TestCase):
    def test_swapped_day_and_month_are_repaired_by_continuity(self):
        s = ["30-06-2019 23:45", "07-01-2019 00:00", "07-01-2019 00:15", "13-07-2019 00:00"]   # 1er juillet écrit « 07-01 »
        dates, repaired = parse_reference_dates(s)
        self.assertEqual([str(d) for d in dates], ["2019-06-30 23:45:00", "2019-07-01 00:00:00",
                                                   "2019-07-01 00:15:00", "2019-07-13 00:00:00"])
        self.assertEqual(repaired, 2)

    def test_a_correct_series_is_left_alone(self):
        s = ["10-01-2019 12:00", "10-01-2019 12:15", "11-01-2019 00:00"]
        dates, repaired = parse_reference_dates(s)
        self.assertEqual(repaired, 0)
        self.assertEqual(str(dates.iloc[-1]), "2019-01-11 00:00:00")


class Cli(unittest.TestCase):
    def test_import_run_validate_from_the_command_line(self):
        with tempfile.TemporaryDirectory() as t:
            t = Path(t)
            build_dataanalysis_mini(t / "src")
            run = lambda *a: subprocess.run([sys.executable, "-m", "raw_to_processed", *a], cwd=ROOT, capture_output=True, text=True)
            out = run("import-dataanalysis", str(t / "src"), str(t / "data"), "--sensor", "Point008=P506")
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertIn("Point009 {'status': 'ignoré'", out.stdout)
            self.assertEqual(run("run", str(t / "data")).returncode, 0)
            val = run("validate", str(t / "data"))
            self.assertEqual(val.returncode, 0, val.stderr)
            self.assertIn("Point008: conforme", val.stdout)
            self.assertIn("Point007: pas de référence", val.stdout)


if __name__ == "__main__":
    unittest.main()
