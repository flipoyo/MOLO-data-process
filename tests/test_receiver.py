import base64
import json
import random
import tempfile
import unittest
from pathlib import Path

from raw_to_processed.datarepo import verify_raw
from raw_to_processed.lab import VirtualLab
from raw_to_processed.operators import iso_to_int
from raw_to_processed.readers import encode_sensor_data
from raw_to_processed.receiver import ingest_messages, parse_uplink
from raw_to_processed.virtual_lab import DEFAULT_CALIBRATION as CAL

LAB = VirtualLab({"schema": 1, "calibrations": {"C": CAL.to_dict()},
                  "sampling_points": {"PointA": {"calibration": "C", "devices": ["DEV-A"]},
                                      "PointB": {"calibration": "C", "devices": ["DEV-B"]}}})


def uplink(ui, when, counts=(100.0, 200.0, 300.0, 400.0, 500.0, 600.0), fcnt=0):
    import pandas as pd
    data = encode_sensor_data(ui, iso_to_int(pd.Timestamp(when).to_pydatetime()), list(counts))
    return json.dumps({"deviceInfo": {"devEui": ui}, "fCnt": fcnt, "data": base64.b64encode(data).decode()}, sort_keys=True)


class Parsing(unittest.TestCase):
    def test_reads_device_and_message_time(self):
        self.assertEqual(parse_uplink(uplink("DEV-A", "2024-10-07T12:15:00")), ("DEV-A", "2024-10-07T12:15:00+00:00"))

    def test_unreadable_messages_raise_value_error(self):
        for bad in ("pas du json", "{}", '{"data": "%%%"}', json.dumps({"data": base64.b64encode(b"\x0a\xff").decode()}),
                    json.dumps({"data": base64.b64encode(b"").decode()}), "[1, 2]", "null"):
            with self.assertRaises(ValueError, msg=bad):
                parse_uplink(bad)

    def test_impossible_date_is_unreadable(self):
        data = encode_sensor_data("DEV-A", 1399990000, [1.0])        # mois 13
        with self.assertRaises(ValueError):
            parse_uplink(json.dumps({"data": base64.b64encode(data).decode()}))


class Ingest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def files(self, sub):
        return sorted(p.name for p in (self.root / "raw_data" / sub).rglob("*.jsonl"))

    def test_routes_by_device_and_by_message_day(self):
        lines = [uplink("DEV-A", "2024-10-07T23:45:00"), uplink("DEV-A", "2024-10-08T00:00:00"),
                 uplink("DEV-B", "2024-10-07T10:00:00"), uplink("INCONNU", "2024-10-07T10:00:00"), "n'importe quoi"]
        report = ingest_messages(self.root, lines, LAB)
        self.assertEqual(report, {"routed": {"PointA": 2, "PointB": 1}, "unrouted": 1, "invalid": 1, "duplicates": 0})
        self.assertEqual(self.files("PointA"), ["2024-10-07.jsonl", "2024-10-08.jsonl"])
        self.assertEqual(self.files("PointB"), ["2024-10-07.jsonl"])
        self.assertEqual(self.files("_unrouted"), ["2024-10-07.jsonl"])
        self.assertEqual(self.files("_invalid"), ["invalid.jsonl"])

    def test_raw_lines_are_kept_byte_for_byte(self):
        line = uplink("DEV-A", "2024-10-07T12:00:00", fcnt=7)
        ingest_messages(self.root, [line], LAB)
        stored = (self.root / "raw_data/PointA/v2_lora/2024-10-07.jsonl").read_text(encoding="utf-8")
        self.assertEqual(stored, line + "\n")

    def test_replaying_the_same_messages_changes_nothing(self):
        lines = [uplink("DEV-A", f"2024-10-07T12:{m:02d}:00", fcnt=m) for m in range(0, 60, 15)]
        ingest_messages(self.root, lines, LAB)
        before = (self.root / "raw_data/PointA/v2_lora/2024-10-07.jsonl").read_bytes()
        report = ingest_messages(self.root, lines, LAB)
        self.assertEqual(report["routed"], {})
        self.assertEqual(report["duplicates"], 4)
        self.assertEqual((self.root / "raw_data/PointA/v2_lora/2024-10-07.jsonl").read_bytes(), before)

    def test_arrival_order_and_batching_do_not_change_the_bytes(self):
        lines = [uplink("DEV-A", f"2024-10-0{d}T{h:02d}:00:00", fcnt=h) for d in (7, 8) for h in range(0, 24, 3)]
        other = self.root / "other"
        ingest_messages(self.root, lines, LAB)
        shuffled = lines[:]
        random.Random(3).shuffle(shuffled)
        ingest_messages(other, shuffled[:5], LAB)
        ingest_messages(other, shuffled[5:], LAB)
        for rel in ("PointA/v2_lora/2024-10-07.jsonl", "PointA/v2_lora/2024-10-08.jsonl", "PointA/ingest.json"):
            self.assertEqual((self.root / "raw_data" / rel).read_bytes(), (other / "raw_data" / rel).read_bytes(), rel)

    def test_files_are_sorted_by_message_time_even_across_months(self):
        lines = [uplink("DEV-A", "2024-10-07T12:00:00"), uplink("DEV-A", "2024-10-07T09:00:00")]
        ingest_messages(self.root, lines, LAB)
        stored = (self.root / "raw_data/PointA/v2_lora/2024-10-07.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual([parse_uplink(x)[1][11:16] for x in stored], ["09:00", "12:00"])

    def test_ingest_json_records_every_raw_file_and_detects_tampering(self):
        ingest_messages(self.root, [uplink("DEV-A", "2024-10-07T12:00:00")], LAB)
        point = self.root / "raw_data/PointA"
        self.assertEqual(verify_raw(point), [])
        recorded = json.loads((point / "ingest.json").read_text(encoding="utf-8"))["files"]
        self.assertEqual(list(recorded), ["v2_lora/2024-10-07.jsonl"])
        (point / "v2_lora/2024-10-07.jsonl").write_text("altéré\n", encoding="utf-8")
        self.assertEqual(verify_raw(point), ["modifié : v2_lora/2024-10-07.jsonl"])
        (point / "v2_lora/extra.jsonl").write_text("x\n", encoding="utf-8")
        self.assertIn("non enregistré : v2_lora/extra.jsonl", verify_raw(point))


if __name__ == "__main__":
    unittest.main()
