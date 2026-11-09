"""Opérateurs purs portés depuis Molonaviz (aucune I/O, aucun état global).

Origine (dépôt MOLONARI1D, branche clonée) :
  - adc_to_volt        <- Molonaviz/src/receiver/decoder.py::conversion_volt
  - int_to_iso8601     <- Molonaviz/src/receiver/decoder.py::int_to_iso8601
  - volt_to_kelvin     <- Molonaviz/src/molonaviz/backend/SPointCoordinator.py::calibrate_temperature
  - volt_to_pressure   <- Molonaviz/src/molonaviz/frontend/dialogsCleanup.py::convertVoltagePressure
  - detect_date_format <- Molonaviz/src/molonaviz/utils/general.py::convertDates
  - iqr/zscore/date    <- Molonaviz/src/molonaviz/frontend/dialogsCleanup.py::applyIQR / applyZScore /
                          applyDateBoundariesChanges

Écarts volontaires par rapport à Molonaviz (voir README) :
  1. adc_to_volt(0) vaut 0.0 (Molonaviz renvoie NaN pour 0 à cause de `if not value`).
  2. volt_to_celsius enchaîne kelvin -> °C. Molonaviz stocke des Kelvin côté receveur alors que le
     nettoyage et Compute.py supposent des °C.
  3. Les dates sont lues avec un format EXPLICITE ; detect_date_format ne sert qu'à le proposer
     (pas de repli `None` non déterministe).
"""
from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd

ADC_MAX = 4095.0   # convertisseur 12 bits
ADC_VREF = 3.3     # tension de référence du convertisseur (V)


# --------------------------------------------------------------------------- conversions
def adc_to_volt(count):
    """Compte du convertisseur (0..4095) -> volts. NaN reste NaN."""
    return np.asarray(count, dtype=float) / ADC_MAX * ADC_VREF


def volt_to_kelvin(volt, beta: float, v_ref: float, t0_k: float = 298.15):
    """Loi bêta d'une thermistance : T = 1 / (1/T0 - ln(V_ref/V - 1) / beta).

    NaN si l'argument du logarithme est <= 0, si V_ref == 0 ou si le dénominateur est nul
    (même comportement que Molonaviz).
    """
    v = np.asarray(volt, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        log_input = v_ref / v - 1.0
        valid = np.isfinite(log_input) & (log_input > 0) & (v_ref != 0)
        denom = 1.0 / t0_k - (1.0 / beta) * np.log(np.where(valid, log_input, 1.0))
        valid = valid & (denom != 0)
        out = np.where(valid, 1.0 / np.where(valid, denom, 1.0), np.nan)
    return out if out.ndim else float(out)


def kelvin_to_celsius(kelvin):
    return np.asarray(kelvin, dtype=float) - 273.15


def volt_to_celsius(volt, beta: float, v_ref: float, t0_k: float = 298.15):
    return kelvin_to_celsius(volt_to_kelvin(volt, beta, v_ref, t0_k))


def volt_to_pressure(voltage, temp_bed_c, intercept: float, dudh: float, dudt: float):
    """Tension -> pression différentielle : (U - T_lit * dU/dT - intercept) / dU/dH.

    T_lit doit être en °C : les coefficients de calibration (fichiers P*.csv) ont été établis ainsi.
    """
    u = np.asarray(voltage, dtype=float)
    t = np.asarray(temp_bed_c, dtype=float)
    return (u - t * dudt - intercept) / dudh


# --------------------------------------------------------------------------- dates
def int_to_iso8601(value: int) -> str:
    """Entier MMJJAAhhmm (UTC supposé, sans secondes) -> ISO 8601."""
    s = f"{int(value):010d}"
    dt = datetime(int(s[4:6]) + 2000, int(s[0:2]), int(s[2:4]), int(s[6:8]), int(s[8:10]), tzinfo=timezone.utc)
    return dt.isoformat()


def iso_to_int(ts: datetime) -> int:
    """Inverse de int_to_iso8601 (pour fabriquer des messages V2)."""
    return int(f"{ts.month:02d}{ts.day:02d}{ts.year % 100:02d}{ts.hour:02d}{ts.minute:02d}")


# Sous-ensemble ordonné des formats rencontrés dans Molonaviz (le premier valide gagne).
KNOWN_DATE_FORMATS = (
    "%m/%d/%y %I:%M:%S %p",   # exports HOBO 2016 (Point034)
    "%m/%d/%y %H:%M:%S",
    "%m/%d/%Y %H:%M:%S",
    "%Y/%m/%d %H:%M",         # exports HOBO 2024
    "%d/%m/%Y %H:%M",
    "%Y-%m-%dT%H:%M:%SZ",
)


def detect_date_format(strings, candidates=KNOWN_DATE_FORMATS) -> str:
    """Premier format de `candidates` qui lit toutes les dates ET donne un ordre croissant.

    Même test que convertDates. Attention : un jour/mois ambigu passe silencieusement.
    """
    s = pd.Series(list(strings))
    for fmt in candidates:
        try:
            parsed = pd.to_datetime(s, format=fmt)
        except (ValueError, TypeError):
            continue
        if parsed.is_monotonic_increasing:
            return fmt
    raise ValueError("Aucun format de date connu ne convient")


def parse_dates(strings, fmt: str, utc_offset_hours: float = 0.0) -> pd.Series:
    """Dates -> datetimes naïfs en UTC, avec format et décalage horaire explicites."""
    parsed = pd.to_datetime(pd.Series(list(strings)), format=fmt)
    return parsed - pd.to_timedelta(utc_offset_hours, unit="h")


# --------------------------------------------------------------------------- nettoyage
def iqr_outlier_mask(series: pd.Series, k: float = 1.5) -> pd.Series:
    q1, q3 = series.quantile(0.25), series.quantile(0.75)
    iqr = q3 - q1
    return (series < q1 - k * iqr) | (series > q3 + k * iqr)


def zscore_outlier_mask(series: pd.Series, threshold: float = 3.0) -> pd.Series:
    arr = series.to_numpy(dtype=float)
    std = np.nanstd(arr)
    if not np.isfinite(std) or std == 0:
        return pd.Series(False, index=series.index)
    return pd.Series(np.abs((arr - np.nanmean(arr)) / std) > threshold, index=series.index)


def outside_window_mask(dates: pd.Series, start, end) -> pd.Series:
    """Lignes hors de [start, end]. Aucune ligne rejetée si la fenêtre est incohérente (comme Molonaviz)."""
    none = pd.Series(False, index=dates.index)
    if start is None or end is None:
        return none
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    if start > dates.max() or end < dates.min() or start > end:
        return none
    return (dates > end) | (dates < start)


RULES = {"iqr": iqr_outlier_mask, "zscore": zscore_outlier_mask}


def clean(df: pd.DataFrame, rules: dict | None = None, window: tuple | None = None):
    """Retire les points rejetés. Chaque règle est évaluée sur le jeu complet (pas en cascade).

    Retourne (df_nettoyé, rapport). Les lignes contenant un NaN sont aussi retirées : pyheatmy n'en veut pas.
    """
    rules = rules or {}
    reject = pd.Series(False, index=df.index)
    report = {}
    for var in sorted(rules):
        mask = RULES[rules[var]](df[var])
        report[f"{var}:{rules[var]}"] = int(mask.sum())
        reject |= mask
    if window is not None:
        mask = outside_window_mask(df["Date"], *window)
        report["window"] = int(mask.sum())
        reject |= mask
    kept = df[~reject]
    report["nan_rows"] = int(kept.isna().any(axis=1).sum())
    return kept.dropna().reset_index(drop=True), report
