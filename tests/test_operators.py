import math
import unittest

import numpy as np
import pandas as pd

from raw_to_processed import operators as op
from raw_to_processed.virtual_lab import DEFAULT_CALIBRATION as CAL


class Conversions(unittest.TestCase):
    def test_adc_bounds(self):
        self.assertEqual(float(op.adc_to_volt(0)), 0.0)          # Molonaviz renvoie NaN ici (écart voulu)
        self.assertAlmostEqual(float(op.adc_to_volt(4095)), 3.3)
        self.assertTrue(math.isnan(float(op.adc_to_volt(float("nan")))))

    def test_beta_law_reference_point(self):
        # V = V_ref / 2  =>  ln(1) = 0  =>  T = T0 = 298.15 K
        self.assertAlmostEqual(op.volt_to_kelvin(1.65, CAL.beta, CAL.v_ref), 298.15, places=9)

    def test_beta_law_invalid_inputs_are_nan(self):
        for v in (0.0, CAL.v_ref, 4.0, float("nan")):
            self.assertTrue(math.isnan(op.volt_to_kelvin(v, CAL.beta, CAL.v_ref)), v)
        self.assertTrue(math.isnan(op.volt_to_kelvin(1.0, CAL.beta, 0.0)))

    def test_beta_law_is_monotonic_decreasing_in_voltage(self):
        t = op.volt_to_celsius(np.linspace(0.5, 2.8, 50), CAL.beta, CAL.v_ref)
        self.assertTrue(np.all(np.diff(t) < 0))

    def test_pressure_formula_inverts_the_sensor_model(self):
        head, temp_c = 0.15, 12.0
        u = CAL.intercept + CAL.dudh * head + CAL.dudt * temp_c
        self.assertAlmostEqual(float(op.volt_to_pressure(u, temp_c, CAL.intercept, CAL.dudh, CAL.dudt)), head, places=12)

    def test_kelvin_instead_of_celsius_shifts_the_pressure(self):
        """Si TempBed arrive en Kelvin (chemin receveur de Molonaviz), la pression est décalée de dU/dT*273,15/dU/dH."""
        u, t = 2.2, 12.0
        good = op.volt_to_pressure(u, t, CAL.intercept, CAL.dudh, CAL.dudt)
        bad = op.volt_to_pressure(u, t + 273.15, CAL.intercept, CAL.dudh, CAL.dudt)
        self.assertAlmostEqual(float(good - bad), CAL.dudt * 273.15 / CAL.dudh, places=12)
        self.assertGreater(float(good - bad), 0.12)


class Dates(unittest.TestCase):
    def test_int_iso_roundtrip(self):
        self.assertEqual(op.int_to_iso8601(1007241200), "2024-10-07T12:00:00+00:00")
        ts = pd.Timestamp("2024-10-07T12:00:00").to_pydatetime()
        self.assertEqual(op.iso_to_int(ts), 1007241200)

    def test_detect_hobo_format(self):
        s = ["06/27/16 12:00:00 PM", "06/27/16 12:15:00 PM", "06/27/16 12:30:00 PM"]
        self.assertEqual(op.detect_date_format(s), "%m/%d/%y %I:%M:%S %p")

    def test_day_month_ambiguity_depends_on_candidate_order_not_on_data(self):
        s = ["01/02/2024 00:00", "02/02/2024 00:00", "03/02/2024 00:00"]
        us, eu = "%m/%d/%Y %H:%M", "%d/%m/%Y %H:%M"
        self.assertEqual(op.detect_date_format(s, (us, eu)), us)
        self.assertEqual(op.detect_date_format(s, (eu, us)), eu)

    def test_explicit_offset_to_utc(self):
        out = op.parse_dates(["06/27/16 01:00:00 PM"], "%m/%d/%y %I:%M:%S %p", utc_offset_hours=1)
        self.assertEqual(out.iloc[0], pd.Timestamp("2016-06-27 12:00:00"))


class Cleaning(unittest.TestCase):
    def setUp(self):
        v = np.linspace(10, 14, 101)
        v[50] = 40.0
        self.s = pd.Series(v)

    def test_iqr_flags_only_the_spike(self):
        self.assertEqual(list(op.iqr_outlier_mask(self.s)[lambda m: m].index), [50])

    def test_zscore_flags_only_the_spike(self):
        self.assertEqual(list(op.zscore_outlier_mask(self.s)[lambda m: m].index), [50])

    def test_window_rejects_outside_points_and_ignores_inconsistent_windows(self):
        dates = pd.Series(pd.date_range("2024-01-01", periods=5, freq="D"))
        self.assertEqual(op.outside_window_mask(dates, "2024-01-02", "2024-01-04").tolist(), [True, False, False, False, True])
        self.assertFalse(op.outside_window_mask(dates, "2024-01-04", "2024-01-02").any())   # début > fin
        self.assertFalse(op.outside_window_mask(dates, "2025-01-01", "2025-02-01").any())   # hors données

    def test_clean_drops_rejected_and_nan_rows(self):
        df = pd.DataFrame({"Date": pd.date_range("2024-01-01", periods=101, freq="h"), "X": self.s})
        df.loc[3, "X"] = float("nan")
        kept, report = op.clean(df, {"X": "iqr"})
        self.assertEqual(len(kept), 101 - 1 - 1)
        self.assertEqual(report, {"X:iqr": 1, "nan_rows": 1})


if __name__ == "__main__":
    unittest.main()
