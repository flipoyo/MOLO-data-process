"""Ligne de commande : python -m raw_to_processed <commande> ...

  ingest-file DATA_ROOT messages.jsonl     range des messages bruts (par exemple ceux stockés par collector.py)
  import-v1   DATA_ROOT POINT SRC_DIR      dépose des exports HOBO (P_measures.csv, T_measures.csv)
  run         DATA_ROOT [POINT ...]        raw_data -> processed_data
  verify      DATA_ROOT [POINT ...]        contrôle que le brut n'a pas été modifié
  listen      DATA_ROOT CONFIG.json        écoute le broker MQTT (non essayé contre un vrai serveur)
  import-dataanalysis SRC DATA_ROOT        importe MOLONARI1D/dataAnalysis (brut, laboratoire, références)
  validate    DATA_ROOT [POINT ...]        compare processed_data aux résultats historiques de Molonaviz
  records     RECORDS.csv [sortie.xlsx]    RECORDS.csv -> Excel (nécessite openpyxl)
  thermal-configure DATA_ROOT             assistant interactif de déclaration thermique

Option commune : --lab chemin/vers/lab.json (défaut : DATA_ROOT/lab/lab.json).
"""
from __future__ import annotations

import argparse
import json
import sys

from .dataanalysis import import_dataanalysis
from .datarepo import import_v1, load_lab, raw_point_dir, run_all, verify_raw
from .receiver import ingest_file, listen
from .validate import compare_all
from .thermal_config import configure_thermal


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="raw_to_processed", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    def add(name, *positionals, nargs=None):
        p = sub.add_parser(name)
        p.add_argument("data_root")
        for pos in positionals:
            p.add_argument(pos)
        if nargs:
            p.add_argument("points", nargs="*")
        p.add_argument("--lab", default=None)
        return p

    add("ingest-file", "messages")
    add("import-v1", "point", "src_dir")
    run = add("run", nargs=True)
    run.add_argument("--source", choices=["auto", "v1", "v2"], default="auto")
    run.add_argument("--no-verify", action="store_true")
    add("verify", nargs=True)
    add("listen", "config")
    add("validate", nargs=True)
    imp = sub.add_parser("import-dataanalysis")
    imp.add_argument("src")
    imp.add_argument("data_root")
    imp.add_argument("--sensor", action="append", default=[], metavar="POINT=CAPTEUR",
                     help="impose le capteur de pression d'un point (par exemple Point035=P505)")
    thermal_cfg = sub.add_parser("thermal-configure")
    thermal_cfg.add_argument("data_root")
    thermal_cfg.add_argument("--lab", default=None)
    rec = sub.add_parser("records")
    rec.add_argument("csv")
    rec.add_argument("xlsx", nargs="?")
    args = parser.parse_args(argv)

    if args.command == "import-dataanalysis":
        sensors = dict(item.split("=", 1) for item in args.sensor)
        for point, info in import_dataanalysis(args.src, args.data_root, sensors).items():
            print(point, info)
        return 0
    if args.command == "records":
        from .records import convertir
        convertir(args.csv, args.xlsx)
        return 0
    if args.command == "thermal-configure":
        configure_thermal(args.data_root, args.lab)
        return 0
    lab = load_lab(args.data_root, args.lab)

    if args.command == "ingest-file":
        print(json.dumps(ingest_file(args.data_root, args.messages, lab), sort_keys=True))
    elif args.command == "import-v1":
        print("copiés :", import_v1(args.data_root, args.point, args.src_dir, lab) or "rien (déjà présents)")
    elif args.command == "run":
        results = run_all(args.data_root, lab, args.points or None, args.source, not args.no_verify)
        for point, manifest in results.items():
            print(f"{point}: " + ("pas de données brutes" if manifest is None
                                  else f"{manifest['step']}  sorties {manifest['outputs']['digest'][:16]}…"))
    elif args.command == "verify":
        bad = False
        for point in args.points or lab.points():
            if raw_point_dir(args.data_root, point).is_dir():
                problems = verify_raw(raw_point_dir(args.data_root, point))
                print(f"{point}: " + ("intact" if not problems else " ; ".join(problems)))
                bad = bad or bool(problems)
        return 1 if bad else 0
    elif args.command == "validate":
        for point, result in compare_all(args.data_root, args.points or lab.points()).items():
            if "variables" not in result:
                print(f"{point}: {result['status']}")
                continue
            detail = "  ".join(f"{n}:{d['niveau']}" for n, d in result["variables"].items() if "niveau" in d)
            print(f"{point}: {result['status']:9s} couverture {result['couverture']:.2f}  {detail}")
    elif args.command == "listen":
        listen(args.config, args.data_root, lab)
    return 0


if __name__ == "__main__":
    sys.exit(main())
