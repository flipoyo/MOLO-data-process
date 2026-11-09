"""Démonstration de bout en bout : python demo.py"""
import tempfile
from pathlib import Path

from raw_to_processed.pipeline import run_step
from raw_to_processed.virtual_lab import DEFAULT_CALIBRATION as CAL, LabConfig, make_counts, write_calibration, write_v1, write_v2


def main():
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        counts = make_counts(LabConfig())
        calib = write_calibration(CAL, tmp / "calibration.json")

        write_v1(counts, CAL, tmp / "v1")                       # fichiers au format HOBO
        write_v2(counts, tmp / "v2", redeliveries=3)            # messages LoRa Protobuf (avec 3 doublons)
        m1, d1 = run_step("v1", tmp / "v1", calib, tmp / "out1")
        m2, d2 = run_step("v2", tmp / "v2", calib, tmp / "out2")

        print("Même vérité synthétique, deux formats bruts :")
        print(f"  V1  sorties {m1['outputs']['digest'][:16]}…  arbre git {m1['outputs']['git_tree'][:12]}…")
        print(f"  V2  sorties {m2['outputs']['digest'][:16]}…  arbre git {m2['outputs']['git_tree'][:12]}…")
        print("  convergence :", "OUI" if m1["outputs"]["digest"] == m2["outputs"]["digest"] else "NON")
        print(f"  manifestes différents (entrées et rapports distincts) : {d1[:12]}… / {d2[:12]}…")

        write_v1(counts, CAL, tmp / "v1_hobo", temp_bias=0.021)  # un logger réel a son propre décalage
        m3, _ = run_step("v1", tmp / "v1_hobo", calib, tmp / "out3")
        print("\nSi le logger V1 a un décalage de 0,021 °C :")
        print("  convergence :", "OUI" if m3["outputs"]["digest"] == m2["outputs"]["digest"] else "NON (attendu : il faut une tolérance)")

        print("\nDébut de la sortie canonique :")
        print("\n".join((tmp / "out1/data/measures.csv").read_text().splitlines()[:4]))


if __name__ == "__main__":
    main()
