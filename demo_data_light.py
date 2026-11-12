"""Démonstration complète : import de dataAnalysis, conversion, contrôle, calibration thermique, reproductibilité.

    python demo_data_light.py --dataanalysis ../MOLONARI1D/dataAnalysis --data-root /chemin/vers/MOLONARI-DATA-light
    python demo_data_light.py                      # sans arguments : petit jeu synthétique, dossier temporaire

`--dataanalysis` : le dossier dataAnalysis de MOLONARI1D (branche main).
`--data-root`    : le dépôt de données où écrire (par défaut un dossier temporaire jeté à la fin).
"""
import argparse
import json
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

from raw_to_processed.dataanalysis import import_dataanalysis
from raw_to_processed.datarepo import load_lab, run_all, run_point
from raw_to_processed.fixtures import build_dataanalysis_mini, build_thermal_point
from raw_to_processed.lab import VirtualLab
from raw_to_processed.thermal_config import configure_thermal
from raw_to_processed.validate import compare_all


def title(text):
    print(f"\n{'=' * 78}\n{text}\n{'=' * 78}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dataanalysis", default=None)
    parser.add_argument("--data-root", default=None)
    args = parser.parse_args()

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        src = Path(args.dataanalysis) if args.dataanalysis else tmp / "dataAnalysis"
        if not args.dataanalysis:
            build_dataanalysis_mini(src)
            print("(Aucun --dataanalysis : jeu synthétique de trois points.)")
        root = Path(args.data_root) if args.data_root else tmp / "MOLONARI-DATA-light"

        title("1. Import : données brutes, laboratoire virtuel (configuration matérielle), références")
        report = import_dataanalysis(src, root)
        for point, info in report.items():
            if info["status"] == "importé":
                print(f"  {point}  capteur {info['capteur']}  dates {info['format_date']}  décalage {info['decalage_h']}  "
                      f"horodatages {info['horodatages']}  ({info['lignes_pression']} / {info['lignes_temperature']} lignes)")
            else:
                print(f"  {point}  IGNORÉ : {info['raison']}")

        title("2. Conversion : raw_data -> processed_data (le même code pour tous les points)")
        results = run_all(root, load_lab(root), raise_errors=False)
        for point, manifest in results.items():
            if manifest and "error" in manifest:
                print(f"  {point}  ERREUR {manifest['error']}")
            elif manifest:
                print(f"  {point}  {manifest['step']}  sorties {manifest['outputs']['digest'][:16]}…")

        title("3. Contrôle : écart avec ce que l'ancienne chaîne (Molonaviz) avait produit")
        print("  conforme = identique à l'arrondi près ; proche = pression à 1e-4 près ; écarts = à expliquer")
        for point, result in compare_all(root, [p for p, m in results.items() if m and "error" not in m]).items():
            if "variables" not in result:
                print(f"  {point}  {result['status']}")
                continue
            worst = result["variables"]["Pressure"]
            print(f"  {point}  {result['status']:9s} couverture {result['couverture']:4.2f}  "
                  f"écart max pression {worst.get('ecart_max', float('nan')):.1e}  "
                  f"dates de référence réparées : {result['dates_reparees_dans_la_reference']}")

        title("4. Calibration thermique (filtre de calib.py) sur un point dont la pression est faussée par la température")
        thermal_root = tmp / "thermal_demo"                    # dépôt jetable : on ne mélange pas de synthétique aux vraies données
        info = build_thermal_point(thermal_root)
        lab = info["lab"]
        without = json.loads(json.dumps(lab.data))
        del without["sampling_points"]["PointThermique"]["thermal_calibration"]
        lab = VirtualLab(without)
        lab.save(thermal_root / "lab" / "lab.json")

        title("4a. Déclaration automatique : le wizard construit lab.json à partir du fichier RECORDS.csv")
        answers = iter([
            "P520_demo_generation",
            "calibration/PointThermique/RECORDS.csv",
            "records",
            "Value_Temperature_2",
            "Value_Temperature_3",
            "Value_Pressure",
            "TempBed",
            "",
            "PointThermique",
            "oui",
        ])
        configure_thermal(thermal_root, input_fn=lambda _prompt: next(answers))
        print("  Configuration créée et validée dans lab/lab.json.")

        title("4b. Traitement avant / après activation de la calibration thermique")
        run_point(thermal_root, "PointThermique", VirtualLab(without.data))
        raw = pd.read_csv(thermal_root / "processed_data/PointThermique/data/measures.csv")["Pressure"].to_numpy()
        manifest, _ = run_point(thermal_root, "PointThermique")
        corrected = pd.read_csv(thermal_root / "processed_data/PointThermique/data/measures.csv")["Pressure"].to_numpy()
        model = manifest["thermal"]["thermal_model"]
        print(f"  calibration lue dans {manifest['thermal']['source']['table']}  "
              f"({manifest['thermal']['n_points']} mesures, pas de {manifest['thermal']['dt_seconds']:.0f} s)")
        print(f"  modèle retrouvé : a={model['a']:.4f} b={model['b']:.3f} g={model['g']:.3f}   "
              f"sensibilité k={manifest['thermal']['pressure_model']['k']:.5f}")
        print(f"  a ramené au pas de {manifest['report']['thermal']['dt_field_s']:.0f} s : {manifest['report']['thermal']['a_applied']:.4f}")
        print(f"  écart à la charge vraie  sans correction : max {np.abs(raw - info['true_head']).max():.2e}")
        print(f"  écart à la charge vraie  avec correction : max {np.abs(corrected - info['true_head']).max():.2e}")

        title("5. Reproductibilité : on relance tout, les empreintes doivent être identiques")
        again = run_all(root, load_lab(root), raise_errors=False)
        same = all(again[p]["outputs"]["digest"] == results[p]["outputs"]["digest"]
                   for p in results if results[p] and "error" not in results[p])
        print("  empreintes des sorties identiques pour tous les points :", "OUI" if same else "NON")
        if args.data_root:
            print(f"\nRésultats dans {root} (raw_data, processed_data, reference_data, lab).")


if __name__ == "__main__":
    main()
