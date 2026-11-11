import json
import tempfile
import unittest
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

from raw_to_processed.datarepo import load_thermal, run_point
from raw_to_processed.fixtures import (CALIBRATION_COLUMNS, THERMAL_TRUTH, build_thermal_point, make_calibration_records)
from raw_to_processed.lab import VirtualLab
from raw_to_processed.records import COLONNES, read_records
from raw_to_processed.thermal import (ThermalCalibration, apply_to_ready_frame, apprendre_modele_thermique,
                                      corriger_pression, estimate_internal_temperature, fit_calibration,
                                      fit_pressure_model, fit_thermal_model, load_calibration_table)


def noisy_table(n=400, noise=0.02):
    """Calibration synthétique avec un bruit déterministe (pas de hasard)."""
    t = np.arange(n)
    te = 20 + 6 * np.sin(2 * np.pi * t / 90) + 3 * np.sin(2 * np.pi * t / 37 + 1.0)
    ti = np.empty(n)
    ti[0] = THERMAL_TRUTH["b"] + THERMAL_TRUTH["g"] * te[0]
    for i in range(1, n):
        ti[i] = THERMAL_TRUTH["a"] * ti[i - 1] + (1 - THERMAL_TRUTH["a"]) * (THERMAL_TRUTH["b"] + THERMAL_TRUTH["g"] * te[i - 1])
    ti = ti + noise * np.sin(1.7 * t)
    return pd.DataFrame({"Température externe": te, "Température interne": ti, "Pression": 0.3 - 0.004 * (ti - 20)})


class Model(unittest.TestCase):
    def test_recovers_the_filter_parameters_from_clean_data(self):
        table = make_calibration_records(Path(tempfile.mkdtemp()) / "RECORDS.csv")
        model = fit_thermal_model(table)
        for key in ("a", "b", "g"):
            self.assertAlmostEqual(model[key], THERMAL_TRUTH[key], places=9)

    def test_same_optimum_as_scipy_least_squares_of_calib_py(self):
        try:
            from scipy.optimize import least_squares
        except ImportError:
            self.skipTest("scipy indisponible")
        table = noisy_table()
        Te, Ti = table["Température externe"].to_numpy(), table["Température interne"].to_numpy()

        def residus(params):                       # calib.py, à l'identique
            a, b, g = params
            return Ti[1:] - (a * Ti[:-1] + (1 - a) * (b + g * Te[:-1]))

        ref = least_squares(residus, x0=[0.9, 0.0, 1.0], bounds=([0.0, -np.inf, -np.inf], [0.99999, np.inf, np.inf])).x
        ours = fit_thermal_model(table)
        for value, expected in zip((ours["a"], ours["b"], ours["g"]), ref):
            self.assertAlmostEqual(value, expected, delta=1e-4 * max(1.0, abs(expected)))

    def test_bounds_of_calib_py_are_respected(self):
        te = np.linspace(10, 30, 50)
        ti = np.cumsum(np.ones(50)) * 0.1 + 10                     # dérive sans lien avec te : a tend vers 1
        a = fit_thermal_model(pd.DataFrame({"Température externe": te, "Température interne": ti}))["a"]
        self.assertLessEqual(a, 0.99999)
        self.assertGreaterEqual(a, 0.0)

    def test_input_checks(self):
        with self.assertRaisesRegex(ValueError, "Pas assez"):
            fit_thermal_model(pd.DataFrame({"Température externe": [1.0, 2, 3], "Température interne": [1.0, 2, 3]}))
        with self.assertRaisesRegex(ValueError, "constante"):
            fit_thermal_model(pd.DataFrame({"Température externe": [5.0] * 10, "Température interne": np.arange(10.0)}))
        with self.assertRaises(ValueError):
            estimate_internal_temperature([1.0, float("nan")], {"a": 0.5, "b": 0, "g": 1})
        with self.assertRaises(ValueError):
            estimate_internal_temperature([[1.0, 2.0]], {"a": 0.5, "b": 0, "g": 1})

    def test_internal_temperature_follows_the_filter(self):
        model = {"a": 0.5, "b": 1.0, "g": 2.0}
        ti = estimate_internal_temperature([10.0, 10.0, 10.0], model)
        self.assertEqual(ti.tolist(), [21.0, 21.0, 21.0])        # régime établi : b + g * Te

    def test_pressure_model_and_original_function_names(self):
        table = make_calibration_records(Path(tempfile.mkdtemp()) / "RECORDS.csv")
        model = apprendre_modele_thermique(table)
        pressure = fit_pressure_model(table, model)
        self.assertAlmostEqual(pressure["k"], -0.004, places=6)
        corrected = corriger_pression(table, table["Pression"], table["Température externe"])
        self.assertEqual(len(corrected), len(table))

    def test_time_step_rescaling_keeps_the_time_constant(self):
        cal = ThermalCalibration({"a": 0.97, "b": 1.5, "g": 0.9}, {"c": 0, "k": -0.004, "T_ref": 20}, 60.0, 300)
        self.assertAlmostEqual(cal.model_for_step(900)["a"], 0.97 ** 15)
        self.assertEqual(cal.model_for_step(60)["a"], 0.97)
        self.assertEqual(cal.model_for_step(None)["a"], 0.97)
        self.assertEqual(ThermalCalibration(cal.thermal_model, cal.pressure_model, None, 1).model_for_step(900)["a"], 0.97)

    def test_calibration_step_is_read_from_the_dates(self):
        table = make_calibration_records(Path(tempfile.mkdtemp()) / "RECORDS.csv")
        self.assertEqual(fit_calibration(table).dt_seconds, 60.0)
        irregular = table.drop(index=[10])
        with self.assertRaisesRegex(ValueError, "régulièrement"):
            fit_calibration(irregular)
        self.assertEqual(fit_calibration(irregular, dt_seconds=60.0).dt_seconds, 60.0)


class Records(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_read_records_format_of_the_sd_card(self):
        path = self.dir / "RECORDS.csv"
        path.write_text("DEV-1; 1; 02/11/2026; 09:00:00; 1.5; 20.1; 20.2; 20.3; 20.4; 20.5;\n"
                        "DEV-1; 2; 02/11/2026; 09:01:00; 1.6; 20.2; 20.3; 20.4; 20.5; 20.6;\n\n", encoding="utf-8")
        df = read_records(path)
        self.assertEqual(list(df.columns), COLONNES + ["Timestamp"])
        self.assertEqual(len(df), 2)
        self.assertEqual(df["Timestamp"].iloc[1], pd.Timestamp("2026-11-02 09:01:00"))
        self.assertEqual(df["Value_Temperature_6"].iloc[0], 20.5)

    def test_invalid_dates_are_kept_and_reported(self):
        path = self.dir / "RECORDS.csv"
        path.write_text("DEV-1; 1; 32/13/2026; 09:00:00; 1.5; 1; 1; 1; 1; 1;\nDEV-1; 2; 02/11/2026; 09:01:00; 1.6; 1; 1; 1; 1; 1;\n", encoding="utf-8")
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            df = read_records(path)
        self.assertEqual(len(df), 2)
        self.assertTrue(pd.isna(df["Timestamp"].iloc[0]))
        self.assertTrue(any("invalide" in str(w.message) for w in caught))

    def test_calibration_table_from_records_requires_the_three_roles(self):
        path = self.dir / "RECORDS.csv"
        make_calibration_records(path)
        table = load_calibration_table(path, CALIBRATION_COLUMNS)
        self.assertEqual(list(table.columns), ["Date", "Température externe", "Température interne", "Pression"])
        with self.assertRaisesRegex(ValueError, "rôles manquants"):
            load_calibration_table(path, {"external": "Value_Temperature_2"})

    def test_calibration_table_with_the_original_column_names(self):
        table = make_calibration_records(self.dir / "RECORDS.csv")
        table.to_csv(self.dir / "cal.csv", index=False)
        loaded = load_calibration_table(self.dir / "cal.csv")
        self.assertEqual(len(loaded), len(table))
        pd.DataFrame({"a": [1]}).to_csv(self.dir / "bad.csv", index=False)
        with self.assertRaisesRegex(ValueError, "colonnes absentes"):
            load_calibration_table(self.dir / "bad.csv")

    def test_excel_export_when_openpyxl_is_available(self):
        try:
            import openpyxl  # noqa: F401
        except ImportError:
            self.skipTest("openpyxl indisponible")
        from raw_to_processed.records import convertir
        make_calibration_records(self.dir / "RECORDS.csv", n=20)
        convertir(self.dir / "RECORDS.csv")
        self.assertEqual(len(pd.read_excel(self.dir / "RECORDS.xlsx")), 20)


class InThePipeline(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "data"
        self.info = build_thermal_point(self.root)
        self.lab = self.info["lab"]

    def tearDown(self):
        self._tmp.cleanup()

    def measures(self):
        return pd.read_csv(self.root / "processed_data/PointThermique/data/measures.csv")

    def lab_without_thermal(self):
        data = json.loads(json.dumps(self.lab.data))
        del data["sampling_points"]["PointThermique"]["thermal_calibration"]
        return VirtualLab(data)

    def test_the_correction_recovers_the_true_head(self):
        run_point(self.root, "PointThermique", self.lab_without_thermal())
        raw_error = (self.measures()["Pressure"].to_numpy() - self.info["true_head"]).std()
        run_point(self.root, "PointThermique")
        corrected_error = np.abs(self.measures()["Pressure"].to_numpy() - self.info["true_head"]).max()
        self.assertGreater(raw_error, 4e-3)                  # sans correction, la dérive thermique est nette
        self.assertLess(corrected_error, 2e-4)               # avec, on retrouve la charge vraie

    def test_manifest_records_the_calibration_and_its_source(self):
        manifest, _ = run_point(self.root, "PointThermique")
        thermal = manifest["thermal"]
        self.assertAlmostEqual(thermal["thermal_model"]["a"], THERMAL_TRUTH["a"], places=6)
        self.assertEqual(thermal["source"]["table"], "calibration/PointThermique/RECORDS.csv")
        self.assertEqual(len(thermal["source"]["sha256"]), 64)
        self.assertEqual(thermal["dt_seconds"], 60.0)
        self.assertEqual(manifest["report"]["thermal"]["dt_field_s"], 900.0)
        self.assertAlmostEqual(manifest["report"]["thermal"]["a_applied"], THERMAL_TRUTH["a"] ** 15)

    def test_a_point_without_thermal_calibration_is_unchanged(self):
        manifest, _ = run_point(self.root, "PointThermique", self.lab_without_thermal())
        self.assertNotIn("thermal", manifest)

    def test_changing_the_calibration_file_changes_the_outputs(self):
        before, _ = run_point(self.root, "PointThermique")
        make_calibration_records(self.root / "calibration/PointThermique/RECORDS.csv",
                                 sensitivity={"k": -0.008, "p0": 0.30})
        after, _ = run_point(self.root, "PointThermique")
        self.assertNotEqual(before["thermal"]["source"]["sha256"], after["thermal"]["source"]["sha256"])
        self.assertNotEqual(before["outputs"]["digest"], after["outputs"]["digest"])
        self.assertEqual(before["inputs"]["digest"], after["inputs"]["digest"])

    def test_rerun_is_identical(self):
        a, da = run_point(self.root, "PointThermique")
        b, db = run_point(self.root, "PointThermique")
        self.assertEqual(a["outputs"], b["outputs"])
        self.assertEqual(da, db)

    def test_irregular_field_series_is_refused_with_a_clear_message(self):
        frame = pd.DataFrame({"Date": pd.to_datetime(["2026-11-02 12:00", "2026-11-02 12:15", "2026-11-02 13:00"]),
                              "Pressure": [0.1, 0.1, 0.1], "TempBed": [14.0, 14.1, 14.2]})
        cal = ThermalCalibration({"a": 0.5, "b": 0.0, "g": 1.0}, {"c": 0, "k": 0.1, "T_ref": 14.0}, 900.0, 10)
        with self.assertRaisesRegex(ValueError, "régulièrement"):
            apply_to_ready_frame(frame, cal)

    def test_lab_without_a_table_is_refused(self):
        data = json.loads(json.dumps(self.lab.data))
        data["sampling_points"]["PointThermique"]["thermal_calibration"] = {"columns": CALIBRATION_COLUMNS}
        with self.assertRaisesRegex(ValueError, "table"):
            VirtualLab(data).thermal("PointThermique")

    def test_missing_calibration_file_is_a_clear_error(self):
        (self.root / "calibration/PointThermique/RECORDS.csv").unlink()
        with self.assertRaises(FileNotFoundError):
            load_thermal(self.root, self.lab, "PointThermique")


if __name__ == "__main__":
    unittest.main()
