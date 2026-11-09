"""Un type par état du cycle de vie. Chaque étape est une fonction d'un type vers le suivant.

    RawV1 ----\
               >--> Converted --> Ready
    RawV2 ----/

Les classes sont figées : on ne modifie jamais un état, on en produit un nouveau.
(Les DataFrames contenus ne sont pas protégés par Python ; la discipline est de ne pas les modifier.)
"""
from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass
from pathlib import Path

import pandas as pd

CONVERTED_COLUMNS = ["Date", "Temp1", "Temp2", "Temp3", "Temp4", "TempBed", "Voltage"]
READY_COLUMNS = ["Date", "Temp1", "Temp2", "Temp3", "Temp4", "TempBed", "Pressure"]


@dataclass(frozen=True)
class Calibration:
    """Paramètres de calibration. Une donnée versionnée, jamais du code."""
    beta: float
    v_ref: float
    intercept: float
    dudh: float
    dudt: float
    t0_k: float = 298.15

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def load(cls, path) -> "Calibration":
        return cls(**json.loads(Path(path).read_text(encoding="utf-8")))

    def save(self, path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), sort_keys=True, indent=2) + "\n", encoding="utf-8")

    @classmethod
    def from_pressure_sensor_csv(cls, path, beta: float, v_ref: float) -> "Calibration":
        """Lit un fichier data/Laboratory/pressure_sensors/P*.csv (Intercept, dU/dH, dU/dT).

        beta et v_ref viennent de la thermistance (README_calibration.md : beta = 3834 K, V = 3,3 V).
        """
        with open(path, newline="", encoding="utf-8-sig") as fh:
            rows = {r[0].strip(): r[1].strip() for r in csv.reader(fh) if len(r) >= 2}
        return cls(beta=beta, v_ref=v_ref, intercept=float(rows["Intercept"]),
                   dudh=float(rows["dU/dH"]), dudt=float(rows["dU/dT"]))


@dataclass(frozen=True)
class RawV1:
    """Exports de loggers HOBO tels que lus : dates encore en texte, valeurs déjà en °C et en volts."""
    pressure: pd.DataFrame      # Date, Voltage, Temp_Stream
    temperature: pd.DataFrame   # Date, Temp1..Temp4


@dataclass(frozen=True)
class RawV2:
    """Messages LoRa décodés, SANS conversion : comptes du convertisseur (c0..c5)."""
    messages: pd.DataFrame      # UI, Date (ISO 8601), c0..c5


@dataclass(frozen=True)
class Converted:
    """Unités physiques (°C, V), dates en UTC, précision canonique, mais pas encore de pression."""
    df: pd.DataFrame            # CONVERTED_COLUMNS


@dataclass(frozen=True)
class Ready:
    """Prêt pour pyheatmy : pression différentielle calculée, points rejetés retirés."""
    df: pd.DataFrame            # READY_COLUMNS
