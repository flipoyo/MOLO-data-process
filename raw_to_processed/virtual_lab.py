"""Laboratoire virtuel : une même vérité synthétique écrite aux formats V1 (HOBO) et V2 (LoRa/Protobuf).

La vérité est exprimée en COMPTES DU CONVERTISSEUR (entiers 0..4095), c'est-à-dire déjà quantifiée.
Les deux formats en dérivent avec les mêmes opérateurs, arrondis à la précision de chacun. Les deux chemins
convergent donc vers le même état final par construction : le test vérifie que le pipeline ne casse pas
cette convergence (unités, dates, doublons, arrondis). Des données réelles ne convergeront pas au bit près.
"""
from __future__ import annotations

import base64
import json
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from .operators import ADC_MAX, ADC_VREF, adc_to_volt, iso_to_int, volt_to_celsius
from .readers import encode_sensor_data
from .states import Calibration

# Coefficients du capteur P520 (data/Laboratory/pressure_sensors/P520.csv), thermistance : README_calibration.md.
DEFAULT_CALIBRATION = Calibration(beta=3834.0, v_ref=3.3, intercept=1.33063041535079,
                                  dudh=5.52770388401728, dudt=0.00249334935671834)
HOBO_DATE_FORMAT = "%m/%d/%y %I:%M:%S %p"


@dataclass(frozen=True)
class LabConfig:
    start: str = "2024-10-07T12:00:00"
    n: int = 288                  # 3 jours à 15 minutes
    step_minutes: int = 15
    ui: str = "VIRT-0001"


def _temperature_to_count(temp_c: np.ndarray, calib: Calibration) -> np.ndarray:
    """Inverse de la loi bêta : température (°C) -> compte du convertisseur."""
    inv = 1.0 / calib.t0_k - 1.0 / (temp_c + 273.15)
    volt = calib.v_ref / (1.0 + np.exp(calib.beta * inv))
    return np.rint(volt / ADC_VREF * ADC_MAX)


def make_counts(cfg: LabConfig = LabConfig(), calib: Calibration = DEFAULT_CALIBRATION) -> pd.DataFrame:
    """Colonnes : Date, c0 (tension pression), c1 (température lit), c2..c5 (thermistances). Sans hasard."""
    i = np.arange(cfg.n)
    hours = i * cfg.step_minutes / 60.0
    dates = pd.date_range(cfg.start, periods=cfg.n, freq=f"{cfg.step_minutes}min")

    river = 12.0 + 3.0 * np.sin(2 * np.pi * hours / 24) + 0.01 * np.sin(37.3 * i)
    amplitudes, lags = [2.4, 1.6, 1.0, 0.6], [1.0, 2.0, 3.0, 4.0]
    sensors = [12.0 + a * np.sin(2 * np.pi * (hours - lag) / 24) + 0.01 * np.sin(11.7 * i + k)
               for k, (a, lag) in enumerate(zip(amplitudes, lags))]
    head = 0.15 + 0.05 * np.sin(2 * np.pi * hours / 72) + 0.01 * np.cos(23.1 * i)
    voltage = calib.intercept + calib.dudh * head + calib.dudt * river

    out = pd.DataFrame({"Date": dates})
    out["c0"] = np.rint(voltage / ADC_VREF * ADC_MAX)
    out["c1"] = _temperature_to_count(river, calib)
    for k, s in enumerate(sensors):
        out[f"c{k + 2}"] = _temperature_to_count(s, calib)
    return out


def write_calibration(calib: Calibration, path) -> Path:
    path = Path(path)
    calib.save(path)
    return path


def write_v1(counts: pd.DataFrame, calib: Calibration, out_dir, temp_bias: float = 0.0) -> Path:
    """Deux fichiers à la manière des exports HOBO : P_measures.csv (tension + température) et T_measures.csv."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    def deg(col):
        return np.round(volt_to_celsius(adc_to_volt(counts[col].to_numpy()), calib.beta, calib.v_ref) + temp_bias, 3)

    volts = np.round(adc_to_volt(counts["c0"].to_numpy()), 5)
    bed = deg("c1")
    temps = [deg(f"c{k}") for k in range(2, 6)]
    dates = counts["Date"].dt.strftime(HOBO_DATE_FORMAT).tolist()

    p = ['"Titre de tracé : virtual_lab"', '#,"Date Heure, GMT+00:00","Tension, V","Temp., °C"']
    t = ['"Titre de tracé : virtual_lab"', '#,"Date Heure, GMT+00:00","Temp., °C","Temp., °C","Temp., °C","Temp., °C"']
    for k, date in enumerate(dates):
        p.append(f"{k + 1},{date},{volts[k]:.5f},{bed[k]:.3f},,,")
        t.append(f"{k + 1},{date}," + ",".join(f"{s[k]:.3f}" for s in temps) + ",,,")
    (out_dir / "P_measures.csv").write_bytes(("\n".join(p) + "\n").encode("utf-8"))
    (out_dir / "T_measures.csv").write_bytes(("\n".join(t) + "\n").encode("utf-8"))
    return out_dir


def write_v2(counts: pd.DataFrame, out_dir, ui: str = "VIRT-0001", redeliveries: int = 0) -> Path:
    """`uplinks.jsonl` : un message JSON par ligne, charge utile Protobuf en base64.

    `redeliveries` rejoue les premiers messages : MQTT livre « au moins une fois », donc des doublons arrivent.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    lines = []
    for k, row in enumerate(counts.itertuples(index=False)):
        meas = [float(getattr(row, f"c{j}")) for j in range(6)]
        data = encode_sensor_data(ui, iso_to_int(row.Date.to_pydatetime()), meas)
        lines.append(json.dumps({"deviceInfo": {"devEui": ui}, "fCnt": k,
                                 "data": base64.b64encode(data).decode("ascii")}, sort_keys=True))
    lines += lines[:redeliveries]
    (out_dir / "uplinks.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out_dir
