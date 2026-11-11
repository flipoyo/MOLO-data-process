import tempfile
import unittest
from pathlib import Path

import pandas as pd

from raw_to_processed.operators import detect_date_formats, parse_dates
from raw_to_processed.readers import read_legacy_csv

HOBO_P = ('\ufeffTitre de tracé : p,,,\n#,"Date Heure, GMT+01:00","Tension, V (LGR S/N: 1, SEN S/N: 1)","Temp., °C (LGR S/N: 1)",,\n'
          "1,06/27/16 12:00:00 PM,1.26787,18.342,,\n2,06/27/16 12:15:00 PM,1.26,18.4,,\n3,Fin de fichier,,,,\n")
TAB_T = ('Titre de tracé : t\t\t\t\t\n#\t"Date Heure, GMT+01:00"\t"Temp., °C (LGR S/N: 2, SEN S/N: 2, LBL: A)"\t"Temp., °C (B)"\t"Temp., °C (C)"\t"Temp., °C (D)"\n'
         "1\t06/27/16 12:00:00 PM\t13.81\t13.83\t13.85\t13.81\n2\t06/27/16 12:15:00 PM\t13.82\t13.84\t13.86\t13.82\n")
NO_INDEX = ("Date Heure,Tension,Temp\n27/06/2016 12:00,1.5,14.1\n27/06/2016 12:15,1.6,14.2\n")


class Reader(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def put(self, pressure, temperature):
        (self.dir / "pressure.csv").write_text(pressure, encoding="utf-8")
        (self.dir / "temperature.csv").write_text(temperature, encoding="utf-8")

    def test_hobo_title_line_bom_trailing_columns_and_end_of_file_row(self):
        self.put(HOBO_P, TAB_T)
        raw = read_legacy_csv(self.dir)
        self.assertEqual(list(raw.pressure.columns), ["Date", "Voltage", "Temp_Stream"])
        self.assertEqual(raw.pressure.to_dict("records")[0], {"Date": "06/27/16 12:00:00 PM", "Voltage": 1.26787, "Temp_Stream": 18.342})
        self.assertEqual(len(raw.pressure), 2)                       # la ligne « Fin de fichier » est ignorée

    def test_tab_separated_file_whose_headers_contain_commas(self):
        self.put(HOBO_P, TAB_T)
        raw = read_legacy_csv(self.dir)
        self.assertEqual(raw.temperature.loc[0].tolist(), ["06/27/16 12:00:00 PM", 13.81, 13.83, 13.85, 13.81])

    def test_file_without_index_column(self):
        self.put(NO_INDEX, "Date Heure,A,B,C,D\n27/06/2016 12:00,1,2,3,4\n27/06/2016 12:15,1,2,3,4\n")
        raw = read_legacy_csv(self.dir)
        self.assertEqual(raw.pressure.loc[1].tolist(), ["27/06/2016 12:15", 1.6, 14.2])

    def test_values_are_never_converted(self):
        self.put(HOBO_P, TAB_T)
        self.assertEqual(read_legacy_csv(self.dir).pressure["Temp_Stream"].tolist(), [18.342, 18.4])

    def test_strict_refuses_different_timestamps_and_intersection_accepts(self):
        self.put(HOBO_P, TAB_T.replace("12:15:00", "12:30:00"))
        with self.assertRaisesRegex(ValueError, "horodatages"):
            read_legacy_csv(self.dir)
        self.assertEqual(len(read_legacy_csv(self.dir, "intersection").temperature), 2)
        with self.assertRaises(ValueError):
            read_legacy_csv(self.dir, "n'importe quoi")

    def test_file_without_a_header_line_is_refused(self):
        self.put("1,2,3\n4,5,6\n", TAB_T)
        with self.assertRaisesRegex(ValueError, "en-tête"):
            read_legacy_csv(self.dir)


class MixedDateFormats(unittest.TestCase):
    """Point034 : un fichier repassé par un tableur commence en 12 h AM/PM et finit en 24 h."""

    def strings(self):
        first = pd.date_range("2016-06-27 12:00", periods=5, freq="15min").strftime("%m/%d/%y %I:%M:%S %p")
        last = pd.date_range("2016-06-27 13:15", periods=4, freq="15min").strftime("%m/%d/%Y %H:%M")
        return list(first) + list(last)

    def test_single_format_when_one_is_enough(self):
        s = pd.date_range("2019-06-17 12:00", periods=4, freq="15min").strftime("%d/%m/%Y %H:%M")
        self.assertEqual(detect_date_formats(s), ["%d/%m/%Y %H:%M"])

    def test_two_formats_for_a_file_that_switches(self):
        self.assertEqual(detect_date_formats(self.strings()), ["%m/%d/%y %I:%M:%S %p", "%m/%d/%Y %H:%M"])

    def test_cascade_parses_every_row_in_order(self):
        parsed = parse_dates(self.strings(), ["%m/%d/%y %I:%M:%S %p", "%m/%d/%Y %H:%M"])
        self.assertTrue(parsed.is_monotonic_increasing)
        self.assertEqual(parsed.iloc[-1], pd.Timestamp("2016-06-27 14:00"))

    def test_ambiguous_day_month_picks_the_reading_without_a_jump_of_months(self):
        first = list(pd.date_range("2016-06-27 12:00", periods=3, freq="15min").strftime("%m/%d/%y %I:%M:%S %p"))
        tail = ["07/12/2016 10:00", "07/12/2016 10:15"]              # 12 juillet (m/j) ou 7 décembre (j/m) ?
        formats = detect_date_formats(first + tail)
        self.assertEqual(formats[1], "%m/%d/%Y %H:%M")

    def test_unreadable_date_names_the_culprit(self):
        with self.assertRaisesRegex(ValueError, "pas une date"):
            parse_dates(["27/06/2016 12:00", "pas une date"], "%d/%m/%Y %H:%M")

    def test_unknown_offset_keeps_the_original_hour_and_known_offset_converts_to_utc(self):
        s = ["27/06/2016 12:00"]
        self.assertEqual(parse_dates(s, "%d/%m/%Y %H:%M", None).iloc[0], pd.Timestamp("2016-06-27 12:00"))
        self.assertEqual(parse_dates(s, "%d/%m/%Y %H:%M", 2.0).iloc[0], pd.Timestamp("2016-06-27 10:00"))


if __name__ == "__main__":
    unittest.main()
