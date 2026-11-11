"""Calibration thermique du capteur de pression (intégration de `calib.py`).

Principe. Le capteur ne voit pas la température de la rivière (externe) mais celle de sa boîte (interne), qui la suit
avec retard. Un filtre du premier ordre relie les deux :

    Ti[n] = a * Ti[n-1] + (1 - a) * (b + g * Te[n-1])

(un produit de convolution avec un noyau exponentiel). Une expérience de calibration (température externe, interne
et pression mesurées ensemble) fixe a, b, g, puis la sensibilité k de la pression à la température interne. Sur le
terrain, on estime Ti à partir de la température de la rivière et on retire `k * (Ti - T_ref)` à la pression.

Différences avec `calib.py`, à valider avec ses auteurs :
  - Le modèle est le même, mais résolu en forme linéaire : Ti[n] = a*Ti[n-1] + c0 + c1*Te[n-1] avec c0 = (1-a)*b et
    c1 = (1-a)*g. Les moindres carrés exacts remplacent `scipy.optimize.least_squares` : même optimum (un test le
    vérifie quand scipy est disponible), mais aucun point de départ ni itération, donc un résultat déterministe, et
    pas de dépendance à scipy. Les bornes de `calib.py` sur `a` ([0 ; 0,99999]) sont respectées.
  - Le calibrage a un pas de temps (`dt_seconds`). Le coefficient `a` est « par échantillon » : sur des données de
    terrain à un autre pas, on le ramène à la même constante de temps avec a' = a ** (dt_terrain / dt_calibration).
    `calib.py` ne le faisait pas (il suppose des pas identiques).
  - Rien ne s'exécute à l'import (la lecture d'un fichier Excel en dur à la fin de `calib.py` a été retirée).
Les noms d'origine (`apprendre_modele_thermique`, ...) sont conservés en fin de fichier.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

EXTERNAL, INTERNAL, PRESSURE = "Température externe", "Température interne", "Pression"
A_MIN, A_MAX = 0.0, 0.99999


def _clean_columns(table: pd.DataFrame, names: list[str]) -> list[np.ndarray]:
    arrays = [table[n].to_numpy(dtype=float) for n in names]
    mask = np.ones(len(arrays[0]), dtype=bool)
    for a in arrays:
        mask &= np.isfinite(a)
    return [a[mask] for a in arrays]


def fit_thermal_model(calibration: pd.DataFrame) -> dict:
    """Paramètres a, b, g du filtre à partir de colonnes 'Température externe' et 'Température interne'."""
    Te, Ti = _clean_columns(calibration, [EXTERNAL, INTERNAL])
    if len(Te) < 4:
        raise ValueError("Pas assez de mesures de calibration.")
    if np.ptp(Te) == 0:
        raise ValueError("La température externe est constante : le modèle n'est pas identifiable.")
    y = Ti[1:]
    X = np.column_stack([Ti[:-1], np.ones(len(y)), Te[:-1]])
    a = float(np.linalg.lstsq(X, y, rcond=None)[0][0])
    if a < A_MIN or a > A_MAX:                       # borne atteinte : on fixe a et on ajuste c0, c1
        a = min(max(a, A_MIN), A_MAX)
        X2 = np.column_stack([np.ones(len(y)), Te[:-1]])
        c0, c1 = np.linalg.lstsq(X2, y - a * Ti[:-1], rcond=None)[0]
    else:
        c0, c1 = np.linalg.lstsq(X, y, rcond=None)[0][1:]
    return {"a": a, "b": float(c0 / (1 - a)), "g": float(c1 / (1 - a))}


def estimate_internal_temperature(external_temperatures, model: dict, initial_temperature=None) -> np.ndarray:
    """Température interne estimée à partir des températures externes (mesures régulièrement espacées)."""
    Te = np.asarray(external_temperatures, dtype=float)
    if Te.ndim != 1:
        raise ValueError("Le vecteur doit être à une dimension.")
    if not np.all(np.isfinite(Te)):
        raise ValueError("Les températures contiennent des valeurs manquantes.")
    a, b, g = model["a"], model["b"], model["g"]
    Ti = np.empty_like(Te)
    if len(Te) == 0:
        return Ti
    Ti[0] = b + g * Te[0] if initial_temperature is None else initial_temperature
    for n in range(1, len(Te)):
        Ti[n] = a * Ti[n - 1] + (1 - a) * (b + g * Te[n - 1])
    return Ti


def fit_pressure_model(calibration: pd.DataFrame, thermal_model: dict) -> dict:
    """Sensibilité thermique de la pression (k) et température de référence."""
    Te, Ti_real, P = _clean_columns(calibration, [EXTERNAL, INTERNAL, PRESSURE])
    Ti_est = estimate_internal_temperature(Te, thermal_model, initial_temperature=Ti_real[0])
    X = np.column_stack([np.ones(len(Ti_est)), Ti_est])
    c, k = np.linalg.lstsq(X, P, rcond=None)[0]
    return {"c": float(c), "k": float(k), "T_ref": float(np.mean(Ti_est))}


@dataclass(frozen=True)
class ThermalCalibration:
    """Résultat d'un calibrage : à enregistrer dans le manifeste, jamais à saisir à la main."""
    thermal_model: dict                 # a, b, g
    pressure_model: dict                # c, k, T_ref
    dt_seconds: float | None            # pas de temps de l'expérience de calibration
    n_points: int
    external_series: str = "TempBed"    # colonne de terrain utilisée comme température externe
    source: dict | None = None          # fichier de calibration utilisé (chemin et SHA-256), pour le manifeste

    def to_dict(self) -> dict:
        return {"thermal_model": self.thermal_model, "pressure_model": self.pressure_model,
                "dt_seconds": self.dt_seconds, "n_points": self.n_points, "external_series": self.external_series,
                "source": self.source}

    def model_for_step(self, dt_target_seconds: float | None) -> dict:
        """Le modèle thermique ramené au pas de temps des données de terrain."""
        model = dict(self.thermal_model)
        if self.dt_seconds and dt_target_seconds:
            model["a"] = model["a"] ** (dt_target_seconds / self.dt_seconds)
        return model

    def correct(self, pressure, external_temperatures, dt_target_seconds: float | None = None,
                initial_temperature=None):
        """(pression corrigée, température interne estimée)."""
        P = np.asarray(pressure, dtype=float)
        Te = np.asarray(external_temperatures, dtype=float)
        if P.shape != Te.shape:
            raise ValueError("Les vecteurs de pression et de température doivent avoir la même taille.")
        Ti = estimate_internal_temperature(Te, self.model_for_step(dt_target_seconds), initial_temperature)
        return P - self.pressure_model["k"] * (Ti - self.pressure_model["T_ref"]), Ti


def calibration_step_seconds(table: pd.DataFrame) -> float | None:
    """Pas de temps d'une table de calibration munie d'une colonne `Date` (None s'il n'y en a pas)."""
    if "Date" not in table.columns:
        return None
    steps = pd.to_datetime(table["Date"]).diff().dropna().dt.total_seconds()
    if steps.empty:
        return None
    if steps.nunique() != 1:
        raise ValueError("Les mesures de calibration ne sont pas régulièrement espacées.")
    return float(steps.iloc[0])


def fit_calibration(table: pd.DataFrame, dt_seconds: float | None = None,
                    external_series: str = "TempBed") -> ThermalCalibration:
    """Ajuste modèle thermique et modèle de pression sur une table de calibration."""
    thermal = fit_thermal_model(table)
    pressure = fit_pressure_model(table, thermal)
    dt = dt_seconds if dt_seconds is not None else calibration_step_seconds(table)
    return ThermalCalibration(thermal, pressure, dt, int(len(table)), external_series)


def load_calibration_table(path, columns: dict | None = None) -> pd.DataFrame:
    """Table de calibration : colonnes 'Température externe', 'Température interne', 'Pression' (+ 'Date').

    - `columns` fourni : le fichier est un RECORDS.csv et `columns` dit quelles colonnes jouent les trois rôles, par
      exemple {"external": "Value_Temperature_2", "internal": "Value_Temperature_3", "pressure": "Value_Pressure"}.
      Il n'y a pas de valeur par défaut : le rôle de chaque capteur ne se devine pas.
    - sinon : un .csv ou .xlsx qui porte déjà les trois colonnes ci-dessus (.xlsx : nécessite openpyxl).
    """
    path = str(path)
    if columns:
        from .records import read_records
        missing = {"external", "internal", "pressure"} - set(columns)
        if missing:
            raise ValueError(f"columns : rôles manquants {sorted(missing)}")
        rec = read_records(path)
        return pd.DataFrame({"Date": rec["Timestamp"], EXTERNAL: rec[columns["external"]],
                             INTERNAL: rec[columns["internal"]], PRESSURE: rec[columns["pressure"]]})
    table = pd.read_excel(path) if path.endswith((".xlsx", ".xls")) else pd.read_csv(path)
    absent = [c for c in (EXTERNAL, INTERNAL, PRESSURE) if c not in table.columns]
    if absent:
        raise ValueError(f"colonnes absentes de {path} : {absent}")
    return table


def apply_to_ready_frame(df: pd.DataFrame, thermal: ThermalCalibration):
    """Corrige la colonne `Pressure` d'une table de terrain (dates régulières, sans valeur manquante).

    Retourne (table corrigée, compte rendu pour le manifeste).
    """
    df = df.sort_values("Date", kind="stable").reset_index(drop=True)
    steps = df["Date"].diff().dropna().dt.total_seconds()
    if steps.empty or steps.nunique() != 1:
        raise ValueError("Correction thermique impossible : les mesures de terrain ne sont pas régulièrement espacées.")
    dt = float(steps.iloc[0])
    corrected, _ = thermal.correct(df["Pressure"], df[thermal.external_series], dt)
    out = df.copy()
    out["Pressure"] = corrected
    report = {"external_series": thermal.external_series, "dt_field_s": dt, "dt_calibration_s": thermal.dt_seconds,
              "a_calibration": thermal.thermal_model["a"], "a_applied": thermal.model_for_step(dt)["a"],
              "k": thermal.pressure_model["k"], "T_ref": thermal.pressure_model["T_ref"]}
    return out, report


# --- noms d'origine de calib.py -------------------------------------------------------------------------------
apprendre_modele_thermique = fit_thermal_model
estimer_temperature_interne = estimate_internal_temperature
apprendre_modele_pression = fit_pressure_model


def corriger_pression(calibration, pressions_reelles, temperatures_externes_reelles, temperature_initiale=None):
    """Comme dans calib.py : calibre puis corrige (sans ramener `a` au pas de temps des données)."""
    thermal = fit_thermal_model(calibration)
    pression = fit_pressure_model(calibration, thermal)
    cal = ThermalCalibration(thermal, pression, None, len(calibration))
    return cal.correct(pressions_reelles, temperatures_externes_reelles, None, temperature_initiale)[0]
