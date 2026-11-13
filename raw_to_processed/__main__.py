"""CLI MOLONARI : import, calibration, traitement et vérification depuis un workspace CGS."""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

from .canonical import file_digest
from .dataanalysis import import_dataanalysis
from .datarepo import import_v1, load_lab, raw_point_dir, run_all, run_point, verify_raw
from .lab import LAB_PATH
from .receiver import ingest_file, listen
from .states import Calibration
from .validate import compare_all
from .thermal_config import configure_thermal


def _data_args(parser):
    parser.add_argument("--data-root", default="data",
                        help="racine de MOLO-data-light dans le bootstrap CGS (défaut : ./data)")
    parser.add_argument("--lab", default=None, help="chemin alternatif vers lab.json")


def _lab_file(data_root, explicit=None):
    return Path(explicit) if explicit else Path(data_root) / LAB_PATH


def _import_calibration(data_root, csv_path, calibration_id, beta, v_ref, lab_path=None):
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", calibration_id):
        raise ValueError("identifiant invalide : utiliser lettres, chiffres, '.', '_' ou '-'")
    source = Path(csv_path)
    if not source.is_file():
        raise FileNotFoundError(source)
    lab = load_lab(data_root, lab_path)
    if calibration_id in lab.data.setdefault("calibrations", {}):
        raise FileExistsError(f"la calibration {calibration_id!r} existe déjà")
    calibration = Calibration.from_pressure_sensor_csv(source, beta=beta, v_ref=v_ref)
    dest = Path(data_root) / "calibration" / "instrumental" / f"{calibration_id}.csv"
    if dest.exists():
        raise FileExistsError(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, dest)
    lab.data["calibrations"][calibration_id] = calibration.to_dict()
    lab.data.setdefault("calibration_sources", {})[calibration_id] = {
        "file": dest.relative_to(Path(data_root)).as_posix(),
        "sha256": file_digest(dest),
        "beta": beta,
        "v_ref": v_ref,
    }
    lab.save(_lab_file(data_root, lab_path))
    return dest, calibration


def _assign_calibration(data_root, point, calibration_id, lab_path=None):
    lab = load_lab(data_root, lab_path)
    if not lab.has_point(point):
        raise KeyError(f"point inconnu : {point}")
    if calibration_id not in lab.data.get("calibrations", {}):
        raise KeyError(f"calibration inconnue : {calibration_id}")
    lab.data["sampling_points"][point]["calibration"] = calibration_id
    lab.save(_lab_file(data_root, lab_path))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="molonari",
        description="Workflow MOLONARI : importer les données et calibrations, traiter et vérifier.")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("import-data", help="importer des exports HOBO V1 dans raw_data")
    p.add_argument("point")
    p.add_argument("src_dir")
    _data_args(p)

    p = sub.add_parser("import-calibration", help="importer un CSV de calibration de capteur de pression")
    p.add_argument("csv")
    p.add_argument("--id", required=True, dest="calibration_id")
    p.add_argument("--beta", type=float, default=3834.0)
    p.add_argument("--v-ref", type=float, default=3.3)
    _data_args(p)

    p = sub.add_parser("assign-calibration", help="associer une calibration à un point")
    p.add_argument("point")
    p.add_argument("calibration_id")
    _data_args(p)

    p = sub.add_parser("process", help="traiter les données brutes d'un point")
    p.add_argument("point")
    p.add_argument("--source", choices=["auto", "v1", "v2"], default="auto")
    p.add_argument("--no-verify", action="store_true")
    _data_args(p)

    p = sub.add_parser("verify", help="vérifier l'intégrité des données brutes")
    p.add_argument("point")
    _data_args(p)

    # Commandes historiques conservées pour compatibilité.
    p = sub.add_parser("ingest-file")
    p.add_argument("data_root")
    p.add_argument("messages")
    p.add_argument("--lab", default=None)
    p = sub.add_parser("import-v1")
    p.add_argument("data_root")
    p.add_argument("point")
    p.add_argument("src_dir")
    p.add_argument("--lab", default=None)
    p = sub.add_parser("run")
    p.add_argument("data_root")
    p.add_argument("points", nargs="*")
    p.add_argument("--source", choices=["auto", "v1", "v2"], default="auto")
    p.add_argument("--no-verify", action="store_true")
    p.add_argument("--lab", default=None)
    p = sub.add_parser("verify-raw")
    p.add_argument("data_root")
    p.add_argument("points", nargs="*")
    p.add_argument("--lab", default=None)
    p = sub.add_parser("listen")
    p.add_argument("data_root")
    p.add_argument("config")
    p.add_argument("--lab", default=None)
    p = sub.add_parser("validate")
    p.add_argument("data_root")
    p.add_argument("points", nargs="*")
    p = sub.add_parser("import-dataanalysis")
    p.add_argument("src")
    p.add_argument("data_root")
    p.add_argument("--sensor", action="append", default=[], metavar="POINT=CAPTEUR")
    p = sub.add_parser("thermal-configure")
    p.add_argument("data_root")
    p.add_argument("--lab", default=None)
    p = sub.add_parser("records")
    p.add_argument("csv")
    p.add_argument("xlsx", nargs="?")

    args = parser.parse_args(argv)

    if args.command == "import-data":
        lab = load_lab(args.data_root, args.lab)
        copied = import_v1(args.data_root, args.point, args.src_dir, lab)
        print(f"{args.point}: fichiers importés : {', '.join(copied) if copied else 'déjà présents'}")
    elif args.command == "import-calibration":
        dest, calib = _import_calibration(args.data_root, args.csv, args.calibration_id,
                                          args.beta, args.v_ref, args.lab)
        print(f"{args.calibration_id}: calibration importée dans {dest}")
        print(json.dumps(calib.to_dict(), sort_keys=True))
    elif args.command == "assign-calibration":
        _assign_calibration(args.data_root, args.point, args.calibration_id, args.lab)
        print(f"{args.point}: calibration active = {args.calibration_id}")
    elif args.command == "process":
        lab = load_lab(args.data_root, args.lab)
        manifest, _ = run_point(args.data_root, args.point, lab, args.source, not args.no_verify)
        print(f"{args.point}: {manifest['step']}")
        print(f"calibration: {lab.calibration_name(args.point)}")
        print(f"résultats: {Path(args.data_root) / 'processed_data' / args.point}")
        print(f"empreinte sorties: {manifest['outputs']['digest']}")
    elif args.command == "verify":
        problems = verify_raw(raw_point_dir(args.data_root, args.point))
        print(f"{args.point}: " + ("intact" if not problems else " ; ".join(problems)))
        return 1 if problems else 0
    elif args.command == "ingest-file":
        lab = load_lab(args.data_root, args.lab)
        print(json.dumps(ingest_file(args.data_root, args.messages, lab), sort_keys=True))
    elif args.command == "import-v1":
        lab = load_lab(args.data_root, args.lab)
        print("copiés :", import_v1(args.data_root, args.point, args.src_dir, lab) or "rien (déjà présents)")
    elif args.command == "run":
        lab = load_lab(args.data_root, args.lab)
        results = run_all(args.data_root, lab, args.points or None, args.source, not args.no_verify)
        for point, manifest in results.items():
            print(f"{point}: " + ("pas de données brutes" if manifest is None
                                  else f"{manifest['step']}  sorties {manifest['outputs']['digest'][:16]}…"))
    elif args.command == "verify-raw":
        lab = load_lab(args.data_root, args.lab)
        bad = False
        for point in args.points or lab.points():
            if raw_point_dir(args.data_root, point).is_dir():
                problems = verify_raw(raw_point_dir(args.data_root, point))
                print(f"{point}: " + ("intact" if not problems else " ; ".join(problems)))
                bad = bad or bool(problems)
        return 1 if bad else 0
    elif args.command == "listen":
        listen(args.config, args.data_root, load_lab(args.data_root, args.lab))
    elif args.command == "validate":
        for point, result in compare_all(args.data_root, args.points or load_lab(args.data_root).points()).items():
            if "variables" not in result:
                print(f"{point}: {result['status']}")
                continue
            detail = "  ".join(f"{n}:{d['niveau']}" for n, d in result["variables"].items() if "niveau" in d)
            print(f"{point}: {result['status']:9s} couverture {result['couverture']:.2f}  {detail}")
    elif args.command == "import-dataanalysis":
        sensors = dict(item.split("=", 1) for item in args.sensor)
        for point, info in import_dataanalysis(args.src, args.data_root, sensors).items():
            print(point, info)
    elif args.command == "thermal-configure":
        configure_thermal(args.data_root, args.lab)
    elif args.command == "records":
        from .records import convertir
        convertir(args.csv, args.xlsx)
    return 0


if __name__ == "__main__":
    sys.exit(main())
