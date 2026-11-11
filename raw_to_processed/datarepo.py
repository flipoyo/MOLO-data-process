"""Disposition du dépôt de données (MOLONARI-DATA) et passage de raw_data à processed_data.

    <data_root>/
      raw_data/<point>/v2_lora/<jour>.jsonl     messages reçus, écrits par le receiver
      raw_data/<point>/v1_hobo/P_measures.csv   fichiers déposés à la main, copiés sans modification
      raw_data/<point>/legacy_csv/pressure.csv, temperature.csv   exports des loggers 2015-2023, copiés tels quels
      raw_data/<point>/ingest.json              empreinte de chaque fichier brut, pour détecter une altération
      raw_data/_unrouted/, raw_data/_invalid/   messages d'appareils inconnus ou illisibles
      processed_data/<point>/data/measures.csv  résultat, lisible par COMPUTO
      processed_data/<point>/manifest.json      entrées, calibration, paramètres, code, environnement, sorties

Le brut est la frontière du déterminisme : tout ce qui est écrit dans processed_data se recalcule à l'identique
à partir de raw_data et du laboratoire virtuel.
"""
from __future__ import annotations

import json
import shutil
from dataclasses import replace
from pathlib import Path

from .canonical import file_digest, list_files
from .lab import LAB_PATH, VirtualLab
from .pipeline import process
from .readers import LEGACY_PRESSURE, LEGACY_TEMPERATURE, read_legacy_csv, read_v1
from .thermal import ThermalCalibration, fit_calibration, load_calibration_table

RAW, PROCESSED = "raw_data", "processed_data"
SOURCES = {"v1": "v1_hobo", "v2": "v2_lora", "legacy_csv": "legacy_csv"}
INGEST = "ingest.json"
V1_FILES = ("P_measures.csv", "T_measures.csv")


class RawIntegrityError(ValueError):
    """Un fichier de raw_data ne correspond plus à ce qui avait été enregistré à l'arrivée."""


def raw_point_dir(data_root, point: str) -> Path:
    return Path(data_root) / RAW / point


def processed_point_dir(data_root, point: str) -> Path:
    return Path(data_root) / PROCESSED / point


def load_lab(data_root, lab_path=None) -> VirtualLab:
    return VirtualLab.load(lab_path if lab_path else Path(data_root) / LAB_PATH)


# --------------------------------------------------------------------------- intégrité du brut
def refresh_ingest(point_dir) -> None:
    """Enregistre l'empreinte de tous les fichiers bruts du point (sauf ingest.json lui-même)."""
    point_dir = Path(point_dir)
    payload = {"schema": 1, "files": list_files(point_dir, exclude={INGEST})}
    (point_dir / INGEST).write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def verify_raw(point_dir) -> list[str]:
    """Liste les écarts entre le contenu actuel du brut et ingest.json (liste vide = intact)."""
    point_dir = Path(point_dir)
    ingest = point_dir / INGEST
    if not ingest.exists():
        return [f"{INGEST} manquant"]
    recorded = json.loads(ingest.read_text(encoding="utf-8"))["files"]
    current = list_files(point_dir, exclude={INGEST})
    problems = [f"modifié : {name}" for name in sorted(recorded) if name in current and current[name] != recorded[name]]
    problems += [f"disparu : {name}" for name in sorted(set(recorded) - set(current))]
    problems += [f"non enregistré : {name}" for name in sorted(set(current) - set(recorded))]
    return problems


# --------------------------------------------------------------------------- import V1
def import_v1(data_root, point: str, src_dir, lab: VirtualLab) -> list[str]:
    """Copie des exports HOBO (P_measures.csv et T_measures.csv) dans raw_data/<point>/v1_hobo/, sans les modifier.

    Un fichier déjà présent et différent n'est jamais écrasé : le brut est en ajout seul.
    """
    if not lab.has_point(point):
        raise ValueError(f"point inconnu du laboratoire : {point!r}")
    src_dir = Path(src_dir)
    missing = [name for name in V1_FILES if not (src_dir / name).is_file()]
    if missing:
        raise FileNotFoundError(f"fichiers manquants dans {src_dir} : {', '.join(missing)}")
    try:
        raw = read_v1(src_dir)
    except Exception as exc:
        raise ValueError(f"fichiers V1 illisibles : {exc}") from exc
    if raw.pressure.empty or raw.temperature.empty:
        raise ValueError("fichiers V1 sans ligne exploitable")

    dest_dir = raw_point_dir(data_root, point) / SOURCES["v1"]
    for name in V1_FILES:
        dest = dest_dir / name
        if dest.exists() and file_digest(dest) != file_digest(src_dir / name):
            raise FileExistsError(f"{dest} existe déjà avec un contenu différent")
    copied = []
    dest_dir.mkdir(parents=True, exist_ok=True)
    for name in V1_FILES:
        if not (dest_dir / name).exists():
            shutil.copyfile(src_dir / name, dest_dir / name)
            copied.append(name)
    refresh_ingest(raw_point_dir(data_root, point))
    return copied


# --------------------------------------------------------------------------- import « legacy »
def register_legacy_csv(data_root, point: str, lab: VirtualLab) -> list[str]:
    """Contrôle les fichiers déjà placés dans raw_data/<point>/legacy_csv/ et enregistre leur empreinte.

    Retourne la liste des fichiers bruts du point, avec leur chemin relatif (par exemple `legacy_csv/pressure.csv`).
    """
    if not lab.has_point(point):
        raise ValueError(f"point inconnu du laboratoire : {point!r}")
    point_dir = raw_point_dir(data_root, point)
    legacy = point_dir / SOURCES["legacy_csv"]
    missing = [n for n in (LEGACY_PRESSURE, LEGACY_TEMPERATURE) if not (legacy / n).is_file()]
    if missing:
        raise FileNotFoundError(f"fichiers manquants dans {legacy} : {', '.join(missing)}")
    raw = read_legacy_csv(legacy, lab.params(point, "legacy_csv").get("timestamps", "strict"))
    if raw.pressure.empty or raw.temperature.empty:
        raise ValueError(f"{point} : fichiers legacy sans ligne exploitable")
    refresh_ingest(point_dir)
    return sorted(json.loads((point_dir / INGEST).read_text(encoding="utf-8"))["files"])


def import_legacy_csv(data_root, point: str, pressure_src, temperature_src, lab: VirtualLab) -> list[str]:
    """Copie deux exports de logger dans raw_data/<point>/legacy_csv/ (octets inchangés), puis les enregistre.

    Un fichier déjà présent et différent n'est jamais écrasé.
    """
    dest_dir = raw_point_dir(data_root, point) / SOURCES["legacy_csv"]
    for src, name in ((pressure_src, LEGACY_PRESSURE), (temperature_src, LEGACY_TEMPERATURE)):
        dest = dest_dir / name
        if dest.exists() and file_digest(dest) != file_digest(src):
            raise FileExistsError(f"{dest} existe déjà avec un contenu différent")
    dest_dir.mkdir(parents=True, exist_ok=True)
    for src, name in ((pressure_src, LEGACY_PRESSURE), (temperature_src, LEGACY_TEMPERATURE)):
        if not (dest_dir / name).exists():
            shutil.copyfile(src, dest_dir / name)
    return register_legacy_csv(data_root, point, lab)


# --------------------------------------------------------------------------- raw -> processed
def load_thermal(data_root, lab: VirtualLab, point: str) -> ThermalCalibration | None:
    """Calibre le filtre thermique du point à partir de sa table de calibration (dans le dépôt de données)."""
    spec = lab.thermal(point)
    if spec is None:
        return None
    table_path = Path(data_root) / spec["table"]
    table = load_calibration_table(table_path, spec.get("columns"))
    thermal = fit_calibration(table, spec.get("dt_seconds"), spec.get("external_series", "TempBed"))
    return replace(thermal, source={"table": spec["table"], "sha256": file_digest(table_path)})


def _present_sources(raw_point: Path) -> list[str]:
    return [kind for kind, sub in SOURCES.items()
            if (raw_point / sub).is_dir() and any(p.is_file() for p in (raw_point / sub).iterdir())]


def run_point(data_root, point: str, lab: VirtualLab | None = None, source: str = "auto",
              verify: bool = True, params: dict | None = None):
    """Lit raw_data/<point>/, écrit processed_data/<point>/. Retourne (manifeste, empreinte du manifeste)."""
    data_root = Path(data_root)
    lab = lab or load_lab(data_root)
    if not lab.has_point(point):
        raise ValueError(f"point inconnu du laboratoire : {point!r}")
    raw_point = raw_point_dir(data_root, point)
    if not raw_point.is_dir():
        raise FileNotFoundError(f"pas de données brutes pour {point} ({raw_point})")
    if verify:
        problems = verify_raw(raw_point)
        if problems:
            raise RawIntegrityError(f"{point} : " + " ; ".join(problems))

    present = _present_sources(raw_point)
    if source == "auto":
        if len(present) != 1:
            raise ValueError(f"{point} : sources présentes {present or 'aucune'} ; précise source='v1' ou 'v2'")
        kind = present[0]
    elif source in SOURCES and source in present:
        kind = source
    else:
        raise ValueError(f"{point} : source {source!r} absente (présentes : {present})")

    thermal = load_thermal(data_root, lab, point)
    extra = {"lab": {"point": point, "calibration": lab.calibration_name(point),
                     "devices": lab.devices(point), "digest": lab.digest()}}
    return process(kind, raw_point / SOURCES[kind], lab.calibration(point), processed_point_dir(data_root, point),
                   {**lab.params(point, kind), **(params or {})}, extra, thermal)


def run_all(data_root, lab: VirtualLab | None = None, points: list[str] | None = None,
            source: str = "auto", verify: bool = True, raise_errors: bool = True) -> dict:
    """Traite chaque point du laboratoire qui a des données brutes. {point: manifeste ou None si rien à traiter}.

    `raise_errors=False` : un point en échec ne bloque pas les autres, son entrée est {"error": "message"}.
    """
    data_root = Path(data_root)
    lab = lab or load_lab(data_root)
    results = {}
    for point in points or lab.points():
        if not raw_point_dir(data_root, point).is_dir():
            results[point] = None
            continue
        try:
            results[point] = run_point(data_root, point, lab, source, verify)[0]
        except Exception as exc:
            if raise_errors:
                raise
            results[point] = {"error": f"{type(exc).__name__}: {exc}"}
    return results
