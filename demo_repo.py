"""Démonstration du dépôt de données : python demo_repo.py"""
import tempfile
from pathlib import Path

from raw_to_processed.datarepo import run_all, verify_raw, raw_point_dir
from raw_to_processed.fixtures import build_data_repo


def main():
    with tempfile.TemporaryDirectory() as t:
        root = Path(t) / "MOLONARI-DATA"
        info = build_data_repo(root)
        print("Receiver (messages LoRa)  :", info["routed"])
        print("Appareil inconnu          :", info["unknown"])
        print("\nArborescence créée :")
        for p in sorted(root.rglob("*")):
            if p.is_file():
                print("  ", p.relative_to(root).as_posix())

        print("\nrun_all : raw_data -> processed_data")
        for point, manifest in run_all(root).items():
            print(f"  {point}: {manifest['step']}  sorties {manifest['outputs']['digest'][:16]}…")

        print("\nContrôle d'intégrité du brut :", {p: verify_raw(raw_point_dir(root, p)) or "intact" for p in ("PointV1", "PointV2")})


if __name__ == "__main__":
    main()
