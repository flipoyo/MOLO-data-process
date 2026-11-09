"""Disposition du dépôt de données (MOLONARI-DATA) et passage de raw_data à processed_data.

    <data_root>/
      raw_data/<point>/v2_lora/<jour>.jsonl     messages reçus, écrits par le receiver
      raw_data/<point>/v1_hobo/P_measures.csv   fichiers déposés à la main, copiés sans modification
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
from pathlib import Path

from .canonical import file_digest, list_files
from .lab import LAB_PATH, VirtualLab
from .pipeline import process
from .readers import read_v1

RAW, PROCESSED = "raw_data", "processed_data"
SOURCES = {"v1": "v1_hobo", "v2": "v2_lora"}
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


# --------------------------------------------------------------------------- raw -> processed
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

    extra = {"lab": {"point": point, "calibration": lab.calibration_name(point),
                     "devices": lab.devices(point), "digest": lab.digest()}}
    return process(kind, raw_point / SOURCES[kind], lab.calibration(point), processed_point_dir(data_root, point),
                   {**lab.params(point, kind), **(params or {})}, extra)


def run_all(data_root, lab: VirtualLab | None = None, points: list[str] | None = None,
            source: str = "auto", verify: bool = True) -> dict:
    """Traite chaque point du laboratoire qui a des données brutes. {point: manifeste ou None si rien à traiter}."""
    data_root = Path(data_root)
    lab = lab or load_lab(data_root)
    results = {}
    for point in points or lab.points():
        if not raw_point_dir(data_root, point).is_dir():
            results[point] = None
            continue
        results[point] = run_point(data_root, point, lab, source, verify)[0]
    return results
