"""Importe MOLONARI1D/dataAnalysis dans un dépôt de données (disposition de datarepo.py).

Ce que fait `import_dataanalysis(src, data_root)` pour chaque point de `src/raw_data/pointNN_jj_mm_aa/` :

  raw_data/Point0NN/legacy_csv/pressure.csv, temperature.csv   les deux exports du logger, octets inchangés
  raw_data/Point0NN/meta/                                      geometrie.txt, fichiers d'info, source_files.json
  reference_data/Point0NN/pression_cleaned.csv, temperature_cleaned.csv
                                                               résultats historiques de Molonaviz : servent à
                                                               contrôler la conversion, jamais d'entrée du calcul
et, une fois pour tout le dépôt :

  lab/hardware/{pressure_sensors,shafts,temperature_sensors}/  la configuration matérielle, octets inchangés
  lab/lab.json                                                 le laboratoire virtuel construit à partir d'elle

Rien n'est deviné en silence : le format des dates, le décalage horaire annoncé par l'en-tête du logger, la
correspondance des horodatages entre les deux fichiers et le capteur de pression de chaque point sont écrits dans
`lab.json`. Un point qu'on ne sait pas importer est signalé dans le compte rendu, pas ignoré.
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from .canonical import file_digest
from .datarepo import import_legacy_csv, refresh_ingest
from .lab import LAB_PATH, VirtualLab
from .operators import detect_date_formats
from .readers import _read_legacy_file
from .states import Calibration

HARDWARE_DIRS = ("pressure_sensors", "shafts", "temperature_sensors")
GMT = re.compile(r"GMT\s*([+-])(\d{2}):(\d{2})")


def point_name(folder: str) -> str | None:
    m = re.match(r"point(\d+)_", folder, re.I)
    return f"Point{int(m.group(1)):03d}" if m else None


def _header_text(path: Path) -> str:
    return "\n".join(path.read_text(encoding="utf-8-sig").splitlines()[:6])


def classify(csv_files: list[Path]) -> tuple[Path, Path]:
    """(fichier de pression, fichier de températures) d'après l'en-tête : la pression porte une tension."""
    pressure = [f for f in csv_files if re.search(r"tension|pressure", _header_text(f), re.I)]
    temperature = [f for f in csv_files if f not in pressure]
    if len(pressure) != 1 or len(temperature) != 1:
        raise ValueError(f"fichiers ambigus : pression {[f.name for f in pressure]}, "
                         f"températures {[f.name for f in temperature]}")
    return pressure[0], temperature[0]


def parse_geometry(path: Path) -> dict:
    """geometrie.txt : capteur de pression, profondeur du lit (cm, souvent vide), profondeurs des thermistances."""
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    out = {"pressure_sensor": None, "river_bed_depth_cm": None, "temperature_depths_cm": []}
    for i, line in enumerate(lines):
        nxt = lines[i + 1].strip() if i + 1 < len(lines) else ""
        if line.startswith("#pressure_sensor"):
            out["pressure_sensor"] = nxt or None
        elif line.startswith("#depth"):
            out["river_bed_depth_cm"] = float(nxt.replace(",", ".")) if nxt and not nxt.startswith("#") else None
        elif line.startswith("#temperature_depths"):
            out["temperature_depths_cm"] = [float(x) if "." in x else int(x) for x in nxt.split(";") if x.strip()]
    return out


def declared_offset_hours(header: str) -> float | None:
    """Décalage horaire annoncé par l'en-tête d'un export HOBO (`GMT+01:00`), None s'il n'y en a pas."""
    m = GMT.search(header)
    if not m:
        return None
    return (1 if m.group(1) == "+" else -1) * (int(m.group(2)) + int(m.group(3)) / 60)


def _copy_hardware(src: Path, data_root: Path) -> dict[str, Path]:
    pressure_files = {}
    for sub in HARDWARE_DIRS:
        for f in sorted((src / "configuration" / sub).glob("*.csv")):
            dest = data_root / "lab" / "hardware" / sub / f.name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(f, dest)
            if sub == "pressure_sensors":
                pressure_files[f.stem.upper()] = dest
    return pressure_files


def import_dataanalysis(src_root, data_root, sensors: dict | None = None, beta: float = 3834.0,
                        v_ref: float = 3.3) -> dict:
    """Importe tous les points de `src_root` (le dossier dataAnalysis). Retourne un compte rendu par point.

    `sensors` : {"Point035": "P505"} pour imposer le capteur d'un point quand les fichiers se contredisent.
    `beta` et `v_ref` (README_calibration.md) ne servent pas aux exports « legacy », dont les températures sont déjà
    en °C ; ils remplissent seulement les champs de la calibration.
    """
    src, data_root = Path(src_root), Path(data_root)
    sensors = sensors or {}
    hardware = _copy_hardware(src, data_root)

    specs, plans, report = {}, {}, {}
    calibrations = {}
    for folder in sorted(p for p in (src / "raw_data").iterdir() if p.is_dir()):
        point = point_name(folder.name)
        if point is None:
            continue
        try:
            csv_files = [f for f in sorted(folder.glob("*.csv")) if not re.search(r"cleaned|info", f.name, re.I)]
            pressure, temperature = classify(csv_files)
            geometry = parse_geometry(folder / "geometrie.txt")
            sensor = (sensors.get(point) or geometry["pressure_sensor"] or "").upper()
            if sensor not in hardware:
                raise ValueError(f"capteur de pression {sensor!r} absent de configuration/pressure_sensors")
            header = _header_text(pressure)
            p_dates = list(_read_legacy_file(pressure, 2, ["Date", "V", "T"])["Date"])
            t_dates = list(_read_legacy_file(temperature, 4, ["Date", "1", "2", "3", "4"])["Date"])
            date_fmt = list(dict.fromkeys(detect_date_formats(p_dates) + detect_date_formats(t_dates)))
            date_fmt = date_fmt[0] if len(date_fmt) == 1 else date_fmt
        except Exception as exc:
            report[point] = {"status": "ignoré", "raison": f"{type(exc).__name__}: {exc}"}
            continue
        offset = declared_offset_hours(header)
        calibrations[sensor] = Calibration.from_pressure_sensor_csv(hardware[sensor], beta, v_ref).to_dict()
        specs[point] = {
            "calibration": sensor, "devices": [],
            "geometry": geometry,
            "hardware": {"pressure_sensor_file": hardware[sensor].relative_to(data_root).as_posix(),
                         "sha256": file_digest(hardware[sensor])},
            "legacy_csv": {"date_fmt": date_fmt, "utc_offset_hours": offset,
                           "source_timezone": "unknown_naive_preserved" if offset is None
                           else f"declared_by_logger_header_GMT{offset:+g}",
                           "timestamps": "strict" if p_dates == t_dates else "intersection"},
        }
        plans[point] = (folder, pressure, temperature)
        report[point] = {"status": "importé", "capteur": sensor, "format_date": date_fmt, "decalage_h": offset,
                         "horodatages": specs[point]["legacy_csv"]["timestamps"],
                         "lignes_pression": len(p_dates), "lignes_temperature": len(t_dates)}

    lab = VirtualLab({"schema": 1, "calibrations": calibrations, "sampling_points": specs})
    lab.save(data_root / LAB_PATH)

    for point, (folder, pressure, temperature) in plans.items():
        import_legacy_csv(data_root, point, pressure, temperature, lab)
        meta = data_root / "raw_data" / point / "meta"
        meta.mkdir(parents=True, exist_ok=True)
        for f in sorted(folder.glob("*")):
            if f.suffix.lower() == ".txt" or re.search(r"info", f.name, re.I) and f.suffix.lower() == ".csv":
                shutil.copyfile(f, meta / f.name)
        (meta / "source_files.json").write_text(json.dumps(
            {"pressure": f"{folder.name}/{pressure.name}", "temperature": f"{folder.name}/{temperature.name}"},
            sort_keys=True, indent=2) + "\n", encoding="utf-8")
        refresh_ingest(data_root / "raw_data" / point)
        ref = data_root / "reference_data" / point
        for kind, target in (("pression", "pression_cleaned.csv"), ("temperature", "temperature_cleaned.csv")):
            found = sorted(folder.glob(f"*{kind}_cleaned.csv"))
            if found:
                ref.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(found[0], ref / target)
    return report
