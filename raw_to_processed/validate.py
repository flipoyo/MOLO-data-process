"""Contrôle de la conversion : compare processed_data aux résultats historiques de Molonaviz.

`reference_data/<point>/pression_cleaned.csv` (dates, température de la rivière, dH) et `temperature_cleaned.csv`
(dates, 4 températures) sont ce que l'ancienne chaîne avait produit. On aligne les instants communs (en tenant
compte du décalage horaire annoncé) et on mesure l'écart variable par variable. Les fichiers de référence ont été
nettoyés (points rejetés) : on compare les instants présents des deux côtés, et on rapporte la couverture.

Trois niveaux, variable par variable :
  - « exact »  : écart ≤ l'arrondi canonique du pipeline (3 décimales pour les températures, 5 pour la pression) ;
  - « proche » : pression seulement, écart ≤ 1e-4. Les constantes de calibration de l'ancienne chaîne diffèrent de
                 quelques 1e-6 V/°C de celles de `configuration/` (écart linéaire en température, mesuré sur
                 Point034 et Point036), très en dessous de l'incertitude de mesure (Sigma_Meas_P = 0,01 V) ;
  - « écart »  : au-delà. À expliquer avant de se fier à la conversion de ce point.

Les références des points 46, 47 et 48 écrivent certaines dates avec le mois avant le jour (« 07-01-2019 » pour le
1er juillet) : l'ancienne chaîne a confondu jour et mois. On les répare par continuité (la lecture qui garde la série
croissante) et on le dit dans le compte rendu.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from .datarepo import processed_point_dir

REFERENCE_DATE_FORMAT = "%d-%m-%Y %H:%M"
TOLERANCE = {"Pressure": 1e-5, "TempBed": 1e-3, "Temp1": 1e-3, "Temp2": 1e-3, "Temp3": 1e-3, "Temp4": 1e-3}
TOLERANCE_CLOSE = {"Pressure": 1e-4}


def parse_reference_dates(strings) -> tuple[pd.Series, int]:
    """Dates de la référence, avec réparation des jours et mois inversés. Retourne (dates, nombre de réparations)."""
    s = pd.Series(list(strings))
    day_first = pd.to_datetime(s, format=REFERENCE_DATE_FORMAT, errors="coerce")
    month_first = pd.to_datetime(s, format="%m-%d-%Y %H:%M", errors="coerce")
    out, repaired, previous = day_first.copy(), 0, None
    for i in range(len(s)):
        candidates = [c for c in (day_first.iloc[i], month_first.iloc[i]) if pd.notna(c)]
        if previous is None:
            pick = day_first.iloc[i]
        else:
            forward = [c for c in candidates if c >= previous]
            pick = min(forward, key=lambda c: c - previous) if forward else day_first.iloc[i]
        if pd.notna(day_first.iloc[i]) and pick != day_first.iloc[i]:
            repaired += 1
        out.iloc[i] = pick
        previous = pick if pd.notna(pick) else previous
    return out, repaired


def _read_reference(path: Path, columns: dict[str, int]) -> pd.DataFrame:
    df = pd.read_csv(path)
    dates, repaired = parse_reference_dates(df.iloc[:, 0])
    out = pd.DataFrame({"Date": dates})
    out.attrs["dates_reparees"] = repaired
    for name, k in columns.items():
        if k < df.shape[1]:                          # certaines références n'ont que 3 profondeurs
            out[name] = pd.to_numeric(df.iloc[:, k], errors="coerce")
    return out


def compare_with_reference(data_root, point: str) -> dict:
    """Écart entre processed_data/<point> et reference_data/<point>. {'status': ..., 'variables': {...}}."""
    data_root = Path(data_root)
    out_dir, ref_dir = processed_point_dir(data_root, point), data_root / "reference_data" / point
    if not (out_dir / "data" / "measures.csv").is_file():
        return {"status": "non traité"}
    if not (ref_dir / "pression_cleaned.csv").is_file() or not (ref_dir / "temperature_cleaned.csv").is_file():
        return {"status": "pas de référence"}
    manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
    offset = manifest["params"].get("utc_offset_hours")

    ours = pd.read_csv(out_dir / "data" / "measures.csv")
    # La référence s'arrête à la minute (après une remise à zéro du logger, nos dates ont des secondes) : on compare à la minute.
    ours["Date"] = pd.to_datetime(ours["Date"].str.rstrip("Z")).dt.floor("min")
    pressure = _read_reference(ref_dir / "pression_cleaned.csv", {"TempBed": 1, "Pressure": 2})
    temperature = _read_reference(ref_dir / "temperature_cleaned.csv", {"Temp1": 1, "Temp2": 2, "Temp3": 3, "Temp4": 4})
    repaired = pressure.attrs.get("dates_reparees", 0)
    ref = pressure.merge(temperature, on="Date", how="outer")
    if offset is not None:                           # nos dates sont en UTC, celles de la référence en heure locale
        ref["Date"] = ref["Date"] - pd.to_timedelta(offset, unit="h")

    joined = ours.merge(ref, on="Date", suffixes=("", "_ref"))
    variables = {}
    for name, tol in TOLERANCE.items():
        if f"{name}_ref" not in joined.columns:
            variables[name] = {"absent_de_la_reference": True, "ok": True}
            continue
        both = joined[[name, f"{name}_ref"]].dropna()
        if both.empty:
            variables[name] = {"instants_communs": 0, "ok": False}
            continue
        diff = (both[name] - both[f"{name}_ref"]).abs()
        worst = float(diff.max())
        level = "exact" if worst <= tol else "proche" if worst <= TOLERANCE_CLOSE.get(name, 0) else "écart"
        variables[name] = {"instants_communs": int(len(both)), "ecart_max": worst, "ecart_moyen": float(diff.mean()),
                           "tolerance": tol, "niveau": level, "ok": level != "écart"}
    n_ref = int(ref["Pressure"].notna().sum())
    common = int(joined["Pressure"].notna().sum()) if len(joined) else 0
    levels = {v.get("niveau") for v in variables.values() if "niveau" in v}
    status = ("écarts" if not all(v["ok"] for v in variables.values()) else
              "proche" if "proche" in levels else "conforme")
    return {"status": status, "instants_references": n_ref, "instants_traites": int(len(ours)),
            "couverture": round(common / n_ref, 4) if n_ref else 0.0, "dates_reparees_dans_la_reference": repaired,
            "variables": variables}


def compare_all(data_root, points: list[str]) -> dict:
    return {p: compare_with_reference(data_root, p) for p in points}
