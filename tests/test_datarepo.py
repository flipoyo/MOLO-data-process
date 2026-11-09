import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from raw_to_processed.datarepo import (RawIntegrityError, import_v1, load_lab, processed_point_dir, raw_point_dir,
                                       run_all, run_point, verify_raw)
from raw_to_processed.fixtures import build_data_repo
from raw_to_processed.lab import LAB_PATH, VirtualLab
from raw_to_processed.virtual_lab import DEFAULT_CALIBRATION as CAL, write_v1

GOLDEN_OUTPUTS_DIGEST = "1a12059ac6e21691cac7141168c08e03134af5d337acaf68f777c22d1b0b56d2"   # même vecteur que test_pipeline
ROOT = Path(__file__).resolve().parents[1]


class Repo(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "data"
        self.info = build_data_repo(self.root)
        self.lab = self.info["lab"]

    def tearDown(self):
        self._tmp.cleanup()


class Layout(Repo):
    def test_fixture_has_the_documented_layout(self):
        rel = sorted(p.relative_to(self.root).as_posix() for p in self.root.rglob("*") if p.is_file())
        self.assertIn("lab/lab.json", rel)
        self.assertIn("raw_data/PointV1/v1_hobo/P_measures.csv", rel)
        self.assertIn("raw_data/PointV1/ingest.json", rel)
        self.assertIn("raw_data/PointV2/v2_lora/2024-10-07.jsonl", rel)
        self.assertIn("raw_data/_unrouted/2024-10-07.jsonl", rel)
        self.assertFalse([r for r in rel if r.startswith("processed_data")])        # rien avant `run`
        self.assertEqual(self.info["routed"]["routed"], {"PointV2": 288})
        self.assertEqual(self.info["unknown"]["unrouted"], 5)


class RunAll(Repo):
    def test_both_sources_reach_the_same_processed_data(self):
        results = run_all(self.root)
        self.assertEqual(set(results), {"PointV1", "PointV2"})
        v1, v2 = results["PointV1"], results["PointV2"]
        self.assertEqual(v1["step"], "v1_to_ready")
        self.assertEqual(v2["step"], "v2_to_ready")
        self.assertEqual(v1["outputs"]["digest"], v2["outputs"]["digest"])
        self.assertEqual(v1["outputs"]["digest"], GOLDEN_OUTPUTS_DIGEST)
        for point in ("PointV1", "PointV2"):
            self.assertTrue((processed_point_dir(self.root, point) / "data" / "measures.csv").is_file())
            self.assertTrue((processed_point_dir(self.root, point) / "manifest.json").is_file())

    def test_unrouted_messages_are_never_processed(self):
        run_all(self.root)
        self.assertFalse((self.root / "processed_data" / "_unrouted").exists())
        self.assertTrue((self.root / "raw_data" / "_unrouted" / "2024-10-07.jsonl").is_file())

    def test_a_lab_point_without_raw_data_is_reported_not_failed(self):
        data = {**self.lab.data, "sampling_points": {**self.lab.data["sampling_points"],
                "PointVide": {"calibration": "P520_T001", "devices": []}}}
        results = run_all(self.root, VirtualLab(data))
        self.assertIsNone(results["PointVide"])
        self.assertIsNotNone(results["PointV2"])

    def test_manifest_carries_the_lab_identity(self):
        manifest, _ = run_point(self.root, "PointV2")
        self.assertEqual(manifest["lab"], {"point": "PointV2", "calibration": "P520_T001",
                                           "devices": ["VIRT-0001"], "digest": self.lab.digest()})
        on_disk = json.loads((self.root / "processed_data/PointV2/manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(on_disk, manifest)

    def test_rerun_on_a_copy_gives_identical_manifest(self):
        copy_root = self.root.parent / "copy"
        shutil.copytree(self.root, copy_root)
        a, da = run_point(self.root, "PointV2")
        b, db = run_point(copy_root, "PointV2")
        self.assertEqual(a["outputs"], b["outputs"])
        self.assertEqual(da, db)

    def test_changing_the_lab_calibration_changes_outputs_and_lab_digest(self):
        before, _ = run_point(self.root, "PointV2")
        data = json.loads(json.dumps(self.lab.data))
        data["calibrations"]["P520_T001"]["intercept"] += 0.01
        after, _ = run_point(self.root, "PointV2", VirtualLab(data))
        self.assertNotEqual(before["outputs"]["digest"], after["outputs"]["digest"])
        self.assertNotEqual(before["lab"]["digest"], after["lab"]["digest"])
        self.assertEqual(before["inputs"]["digest"], after["inputs"]["digest"])

    def test_lab_params_are_applied(self):
        data = json.loads(json.dumps(self.lab.data))
        data["sampling_points"]["PointV2"]["params"] = {"window": ["2024-10-08T00:00:00", "2024-10-08T23:45:00"]}
        manifest, _ = run_point(self.root, "PointV2", VirtualLab(data))
        self.assertEqual(manifest["report"]["window"], 288 - 96)


class RawGuards(Repo):
    def test_tampered_raw_is_refused_unless_verification_is_disabled(self):
        target = raw_point_dir(self.root, "PointV2") / "v2_lora" / "2024-10-08.jsonl"
        lines = target.read_text(encoding="utf-8").splitlines()
        target.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")
        with self.assertRaises(RawIntegrityError):
            run_point(self.root, "PointV2")
        manifest, _ = run_point(self.root, "PointV2", verify=False)
        self.assertEqual(manifest["report"]["nan_rows"], 0)

    def test_both_sources_present_requires_an_explicit_choice(self):
        with tempfile.TemporaryDirectory() as t:
            write_v1(self.info["counts"], CAL, Path(t) / "v1")
            import_v1(self.root, "PointV2", Path(t) / "v1", self.lab)
        with self.assertRaisesRegex(ValueError, "précise source"):
            run_point(self.root, "PointV2")
        a, _ = run_point(self.root, "PointV2", source="v2")
        b, _ = run_point(self.root, "PointV2", source="v1", params={"date_fmt": "%m/%d/%y %I:%M:%S %p"})
        self.assertEqual(a["outputs"]["digest"], b["outputs"]["digest"])

    def test_unknown_point_and_missing_raw_are_clear_errors(self):
        with self.assertRaisesRegex(ValueError, "inconnu"):
            run_point(self.root, "Nope")
        data = {**self.lab.data, "sampling_points": {**self.lab.data["sampling_points"],
                "PointVide": {"calibration": "P520_T001", "devices": []}}}
        with self.assertRaises(FileNotFoundError):
            run_point(self.root, "PointVide", VirtualLab(data))


class ImportV1(Repo):
    def make_src(self, name="src"):
        src = Path(self._tmp.name) / name
        write_v1(self.info["counts"], CAL, src)
        return src

    def test_files_are_copied_unchanged_and_import_is_idempotent(self):
        src = self.make_src()
        self.assertEqual(import_v1(self.root, "PointV1", src, self.lab), [])          # déjà importés par la fixture
        dest = raw_point_dir(self.root, "PointV1") / "v1_hobo" / "P_measures.csv"
        self.assertEqual(dest.read_bytes(), (src / "P_measures.csv").read_bytes())
        self.assertEqual(verify_raw(raw_point_dir(self.root, "PointV1")), [])

    def test_a_different_file_is_never_overwritten(self):
        src = self.make_src()
        (src / "P_measures.csv").write_text((src / "P_measures.csv").read_text(encoding="utf-8") + "x\n", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            import_v1(self.root, "PointV1", src, self.lab)

    def test_unreadable_or_incomplete_files_copy_nothing(self):
        bad = Path(self._tmp.name) / "bad"
        bad.mkdir()
        (bad / "P_measures.csv").write_text("seulement une ligne\n", encoding="utf-8")
        (bad / "T_measures.csv").write_text("seulement une ligne\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            import_v1(self.root, "PointV2", bad, self.lab)
        self.assertFalse((raw_point_dir(self.root, "PointV2") / "v1_hobo").exists())
        (bad / "T_measures.csv").unlink()
        with self.assertRaises(FileNotFoundError):
            import_v1(self.root, "PointV2", bad, self.lab)
        with self.assertRaisesRegex(ValueError, "inconnu"):
            import_v1(self.root, "Nope", self.make_src("ok"), self.lab)


class Cli(Repo):
    def run_cli(self, *args):
        return subprocess.run([sys.executable, "-m", "raw_to_processed", *args], cwd=ROOT, capture_output=True, text=True)

    def test_run_then_verify(self):
        out = self.run_cli("run", str(self.root))
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("PointV1: v1_to_ready", out.stdout)
        self.assertIn("PointV2: v2_to_ready", out.stdout)
        ok = self.run_cli("verify", str(self.root))
        self.assertEqual(ok.returncode, 0, ok.stderr)
        raw = raw_point_dir(self.root, "PointV1") / "v1_hobo" / "T_measures.csv"
        raw.write_text(raw.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        bad = self.run_cli("verify", str(self.root))
        self.assertEqual(bad.returncode, 1)
        self.assertIn("modifié : v1_hobo/T_measures.csv", bad.stdout)

    def test_ingest_file_command(self):
        replay = Path(self._tmp.name) / "replay.jsonl"
        replay.write_text((raw_point_dir(self.root, "PointV2") / "v2_lora" / "2024-10-07.jsonl").read_text(encoding="utf-8"),
                          encoding="utf-8")
        out = self.run_cli("ingest-file", str(self.root), str(replay))
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(json.loads(out.stdout)["routed"], {})                   # tout était déjà là


if __name__ == "__main__":
    unittest.main()
