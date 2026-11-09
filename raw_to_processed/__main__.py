"""Ligne de commande : python -m raw_to_processed <commande> ...

  ingest-file DATA_ROOT messages.jsonl     range des messages bruts (par exemple ceux stockés par collector.py)
  import-v1   DATA_ROOT POINT SRC_DIR      dépose des exports HOBO (P_measures.csv, T_measures.csv)
  run         DATA_ROOT [POINT ...]        raw_data -> processed_data
  verify      DATA_ROOT [POINT ...]        contrôle que le brut n'a pas été modifié
  listen      DATA_ROOT CONFIG.json        écoute le broker MQTT (non essayé contre un vrai serveur)

Option commune : --lab chemin/vers/lab.json (défaut : DATA_ROOT/lab/lab.json).
"""
from __future__ import annotations

import argparse
import json
import sys

from .datarepo import import_v1, load_lab, raw_point_dir, run_all, verify_raw
from .receiver import ingest_file, listen


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
    args = parser.parse_args(argv)
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
    elif args.command == "listen":
        listen(args.config, args.data_root, lab)
    return 0


if __name__ == "__main__":
    sys.exit(main())
