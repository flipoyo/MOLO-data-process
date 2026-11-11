"""Les étapes : RawV1/RawV2 -> Converted -> Ready, puis `run_step` qui écrit les sorties et le manifeste."""
from __future__ import annotations

import json
import platform
from pathlib import Path

import numpy as np
import pandas as pd

from . import __version__
from .canonical import DECIMALS, digest, dir_digest, git_tree_hash, list_files, write_ready_csv
from .operators import adc_to_volt, clean, parse_dates, volt_to_celsius, volt_to_pressure
from .readers import read_legacy_csv, read_v1, read_v2
from .states import CONVERTED_COLUMNS, Calibration, Converted, RawV1, RawV2, Ready
from .thermal import ThermalCalibration, apply_to_ready_frame

DEFAULT_PARAMS = {"date_fmt": "%m/%d/%y %I:%M:%S %p", "utc_offset_hours": 0.0,
                  "dedupe": True, "rules": {}, "window": None}
TEMPS = ["Temp1", "Temp2", "Temp3", "Temp4", "TempBed"]


def _quantize(df: pd.DataFrame) -> pd.DataFrame:
    """Précision canonique, appliquée dès l'état Converted : celle du format le plus grossier (le HOBO)."""
    out = df.copy()
    for col in TEMPS + ["Voltage"]:
        out[col] = out[col].round(DECIMALS[col])
    return out[CONVERTED_COLUMNS]


# --------------------------------------------------------------------------- étapes
def v1_to_converted(raw: RawV1, date_fmt: str, utc_offset_hours: float | None = 0.0, dedupe: bool = True) -> Converted:
    p, t = raw.pressure.copy(), raw.temperature.copy()
    p["Date"] = parse_dates(p["Date"], date_fmt, utc_offset_hours)
    t["Date"] = parse_dates(t["Date"], date_fmt, utc_offset_hours)
    df = t.merge(p, on="Date", how="inner").rename(columns={"Temp_Stream": "TempBed"})
    df = df.sort_values("Date", kind="stable")
    if dedupe:
        df = df.drop_duplicates(subset="Date", keep="first")
    return Converted(_quantize(df.reset_index(drop=True)))


def v2_to_converted(raw: RawV2, calib: Calibration, dedupe: bool = True) -> Converted:
    """Canaux : c0 = tension du capteur de pression, c1 = température du lit, c2..c5 = thermistances 1..4."""
    m = raw.messages.copy()
    m["Date"] = pd.to_datetime(m["Date"], format="ISO8601", utc=True).dt.tz_localize(None)
    m = m.sort_values("Date", kind="stable")
    if dedupe:
        m = m.drop_duplicates(subset=["UI", "Date"], keep="first")
    volts = {f"v{i}": adc_to_volt(m[f"c{i}"].to_numpy()) for i in range(6)}

    def celsius(v):
        return volt_to_celsius(v, calib.beta, calib.v_ref, calib.t0_k)

    df = pd.DataFrame({"Date": m["Date"].to_numpy(),
                       "Temp1": celsius(volts["v2"]), "Temp2": celsius(volts["v3"]),
                       "Temp3": celsius(volts["v4"]), "Temp4": celsius(volts["v5"]),
                       "TempBed": celsius(volts["v1"]), "Voltage": volts["v0"]})
    return Converted(_quantize(df))


def converted_to_ready(conv: Converted, calib: Calibration, rules=None, window=None,
                       thermal: ThermalCalibration | None = None):
    """Pression différentielle, correction thermique facultative, puis nettoyage.

    La correction thermique (filtre de calib.py) se fait AVANT le nettoyage : elle exige des mesures régulièrement
    espacées et sans valeur manquante, ce que le nettoyage peut défaire.
    """
    df = conv.df.copy()
    df["Pressure"] = volt_to_pressure(df["Voltage"], df["TempBed"], calib.intercept, calib.dudh, calib.dudt)
    thermal_report = None
    if thermal is not None:
        df, thermal_report = apply_to_ready_frame(df, thermal)
    df = df.drop(columns="Voltage")
    cleaned, report = clean(df, rules, window)
    if thermal_report is not None:
        report["thermal"] = thermal_report
    return Ready(cleaned), report


# --------------------------------------------------------------------------- manifeste
def package_digest() -> str:
    """Empreinte du code de ce paquet : le manifeste dit quelle version a produit les sorties."""
    return dir_digest(Path(__file__).parent, suffixes={".py"})


def process(kind: str, raw_dir, calib: Calibration, out_dir, params: dict | None = None, extra: dict | None = None,
            thermal: ThermalCalibration | None = None):
    """Applique l'étape `kind` ('v1', 'v2' ou 'legacy_csv') et écrit out_dir/data/measures.csv + out_dir/manifest.json.

    Retourne (manifeste, empreinte du manifeste). `outputs.digest` est la preuve de reproductibilité :
    il ne dépend ni de la date, ni de la machine, ni de l'emplacement des dossiers.
    `extra` ajoute des clés au manifeste (par exemple l'identité du laboratoire virtuel).
    """
    p = {**DEFAULT_PARAMS, **(params or {})}
    raw_dir, out_dir = Path(raw_dir), Path(out_dir)
    window = tuple(p["window"]) if p["window"] else None

    naive_dates = False
    if kind == "v1":
        converted = v1_to_converted(read_v1(raw_dir), p["date_fmt"], p["utc_offset_hours"], p["dedupe"])
    elif kind == "legacy_csv":
        raw = read_legacy_csv(raw_dir, p.get("timestamps", "strict"))
        converted = v1_to_converted(raw, p["date_fmt"], p["utc_offset_hours"], p["dedupe"])
        naive_dates = p["utc_offset_hours"] is None       # fuseau inconnu : on garde l'heure d'origine
    elif kind == "v2":
        converted = v2_to_converted(read_v2(raw_dir), calib, p["dedupe"])
    else:
        raise ValueError(f"étape inconnue : {kind!r}")
    ready, report = converted_to_ready(converted, calib, p["rules"], window, thermal)

    data_dir = out_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    write_ready_csv(ready.df, data_dir / "measures.csv", utc=not naive_dates)

    manifest = {
        "schema": 1, "step": f"{kind}_to_ready", "twin_version": __version__,
        "inputs": {"files": list_files(raw_dir), "digest": dir_digest(raw_dir)},
        "calibration": {"values": calib.to_dict(), "sha256": digest(calib.to_dict())},
        "params": {k: p[k] for k in sorted(p) if not (kind == "v2" and k in ("date_fmt", "utc_offset_hours"))},
        "code": {"package_digest": package_digest()},
        "env": {"python": platform.python_version(), "pandas": pd.__version__, "numpy": np.__version__},
        "report": report,
        "outputs": {"files": list_files(data_dir), "digest": dir_digest(data_dir), "git_tree": git_tree_hash(data_dir)},
    }
    if thermal is not None:
        manifest["thermal"] = thermal.to_dict()
    manifest.update(extra or {})
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, sort_keys=True, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return manifest, digest(manifest)


def run_step(kind: str, raw_dir, calib_path, out_dir, params: dict | None = None):
    """Comme `process`, avec la calibration lue dans un fichier JSON."""
    return process(kind, raw_dir, Calibration.load(calib_path), out_dir, params)
