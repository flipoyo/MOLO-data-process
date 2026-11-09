import tempfile
import unittest
from pathlib import Path

import pandas as pd

from raw_to_processed.pipeline import run_step
from raw_to_processed.virtual_lab import (DEFAULT_CALIBRATION as CAL, LabConfig, _temperature_to_count, make_counts,
                              write_calibration, write_v1, write_v2)

# Vecteurs de référence : empreintes des sorties du laboratoire virtuel par défaut (LabConfig()).
# Si l'un change, soit un opérateur a changé de comportement, soit l'arrondi/format canonique a changé :
# c'est à décider consciemment, jamais à corriger en silence.
GOLDEN_OUTPUTS_DIGEST = "1a12059ac6e21691cac7141168c08e03134af5d337acaf68f777c22d1b0b56d2"
GOLDEN_GIT_TREE = "9b7917cd90fa6a8c4aab5a1555b0e33c973600cc"


class Lab(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.counts = make_counts(LabConfig())
        self.calib = write_calibration(CAL, self.tmp / "calibration.json")

    def tearDown(self):
        self._tmp.cleanup()

    def run_both(self, counts=None, v1_kwargs=None, v2_kwargs=None, params=None, tag=""):
        counts = self.counts if counts is None else counts
        write_v1(counts, CAL, self.tmp / f"v1{tag}", **(v1_kwargs or {}))
        write_v2(counts, self.tmp / f"v2{tag}", **(v2_kwargs or {}))
        m1, _ = run_step("v1", self.tmp / f"v1{tag}", self.calib, self.tmp / f"out1{tag}", params)
        m2, _ = run_step("v2", self.tmp / f"v2{tag}", self.calib, self.tmp / f"out2{tag}", params)
        return m1, m2

    def read(self, name):
        return pd.read_csv(self.tmp / name / "data" / "measures.csv")


class Convergence(Lab):
    def test_v1_and_v2_reach_the_same_final_state(self):
        m1, m2 = self.run_both()
        self.assertEqual(m1["outputs"]["digest"], m2["outputs"]["digest"])
        self.assertEqual(m1["outputs"]["git_tree"], m2["outputs"]["git_tree"])
        self.assertEqual((self.tmp / "out1/data/measures.csv").read_bytes(), (self.tmp / "out2/data/measures.csv").read_bytes())

    def test_redelivered_messages_are_removed_by_dedupe(self):
        m1, m2 = self.run_both(v2_kwargs={"redeliveries": 3})
        self.assertEqual(m1["outputs"]["digest"], m2["outputs"]["digest"])

    def test_without_dedupe_the_duplicates_change_the_hash(self):
        m1, m2 = self.run_both(v2_kwargs={"redeliveries": 3}, params={"dedupe": False})
        self.assertNotEqual(m1["outputs"]["digest"], m2["outputs"]["digest"])
        self.assertEqual(len(self.read("out2")), len(self.read("out1")) + 3)

    def test_an_outlier_is_removed_identically_on_both_paths(self):
        counts = self.counts.copy()
        counts.loc[100, "c2"] = _temperature_to_count(pd.Series([30.0]).to_numpy(), CAL)[0]
        m1, m2 = self.run_both(counts, params={"rules": {"Temp1": "iqr"}})
        self.assertEqual(m1["report"]["Temp1:iqr"], 1)
        self.assertEqual(m1["outputs"]["digest"], m2["outputs"]["digest"])
        self.assertEqual(len(self.read("out1")), len(counts) - 1)

    def test_a_logger_offset_breaks_the_hash_but_stays_within_tolerance(self):
        """Un vrai HOBO a sa propre calibration : l'égalité au bit près n'est pas réaliste, un écart borné l'est."""
        m1, m2 = self.run_both(v1_kwargs={"temp_bias": 0.021})
        self.assertNotEqual(m1["outputs"]["digest"], m2["outputs"]["digest"])
        a, b = self.read("out1"), self.read("out2")
        for col in ("Temp1", "Temp2", "Temp3", "Temp4", "TempBed"):
            self.assertLessEqual((a[col] - b[col]).abs().max(), 0.0215)
            self.assertGreaterEqual((a[col] - b[col]).abs().min(), 0.0205)

    def test_wrong_utc_offset_is_visible_in_the_output(self):
        m1, m2 = self.run_both(params={"utc_offset_hours": 0.0})
        shifted, _ = run_step("v1", self.tmp / "v1", self.calib, self.tmp / "out_shift", {"utc_offset_hours": 1.0})
        self.assertNotEqual(m1["outputs"]["digest"], shifted["outputs"]["digest"])


class Determinism(Lab):
    def test_rerun_gives_same_outputs_and_same_manifest(self):
        write_v2(self.counts, self.tmp / "raw")
        a, da = run_step("v2", self.tmp / "raw", self.calib, self.tmp / "run_a")
        b, db = run_step("v2", self.tmp / "raw", self.calib, self.tmp / "elsewhere" / "run_b")
        self.assertEqual(a["outputs"], b["outputs"])
        self.assertEqual(da, db)

    def test_changing_a_calibration_value_changes_inputs_and_outputs(self):
        write_v2(self.counts, self.tmp / "raw")
        a, _ = run_step("v2", self.tmp / "raw", self.calib, self.tmp / "run_a")
        other = write_calibration(type(CAL)(**{**CAL.to_dict(), "intercept": CAL.intercept + 0.01}), self.tmp / "calibration2.json")
        b, _ = run_step("v2", self.tmp / "raw", other, self.tmp / "run_b")
        self.assertNotEqual(a["calibration"]["sha256"], b["calibration"]["sha256"])
        self.assertNotEqual(a["outputs"]["digest"], b["outputs"]["digest"])
        self.assertEqual(a["inputs"]["digest"], b["inputs"]["digest"])


class GoldenVectors(Lab):
    def test_default_virtual_lab_matches_the_frozen_digests(self):
        m1, m2 = self.run_both()
        self.assertEqual(m1["outputs"]["digest"], GOLDEN_OUTPUTS_DIGEST)
        if m1["outputs"]["git_tree"] is None:
            self.skipTest("git indisponible")
        self.assertEqual(m1["outputs"]["git_tree"], GOLDEN_GIT_TREE)


if __name__ == "__main__":
    unittest.main()
