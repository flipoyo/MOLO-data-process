"""Démo guidée : déclarer cinq mesures de calibration fictives dans le lab.json local.

Depuis le dépôt MOLO-data-process :
    pixi run python demo_calibration_thermique.py
"""
from __future__ import annotations

from pathlib import Path

from raw_to_processed.records import read_records
from raw_to_processed.thermal_config import configure_thermal


DEMO_RELATIVE_PATH = Path("calibration/thermal/demo_5_mesures/RECORDS.csv")


def main() -> int:
    print("\n" + "=" * 72)
    print("DÉMONSTRATION — CALIBRATION THERMIQUE AVEC 5 MESURES FICTIVES")
    print("=" * 72)
    print("Cette démonstration modifie le véritable lab/lab.json du dépôt choisi.")
    print("Les cinq mesures sont artificielles : elles servent uniquement à tester le parcours.")
    print("Une sauvegarde horodatée du JSON sera créée avant toute modification.\n")

    raw_root = input("Chemin du dépôt local MOLO-data-light : ").strip()
    if not raw_root:
        print("Aucun chemin fourni. Arrêt sans modification.")
        return 1

    root = Path(raw_root).expanduser().resolve()
    lab_path = root / "lab" / "lab.json"
    calibration_path = root / DEMO_RELATIVE_PATH

    if not lab_path.is_file():
        print(f"Fichier laboratoire introuvable : {lab_path}")
        print("Vérifie le chemin du dépôt MOLO-data-light.")
        return 1
    if not calibration_path.is_file():
        print(f"Jeu de démonstration introuvable : {calibration_path}")
        print("Récupère d'abord le fichier calibration/thermal/demo_5_mesures/RECORDS.csv")
        print("depuis la branche CGS-MOLO du dépôt MOLO-data-light.")
        return 1

    records = read_records(calibration_path)
    if len(records) != 5 or records["Timestamp"].isna().any():
        print("Le fichier de démonstration doit contenir exactement cinq mesures datées.")
        return 1

    print(f"\nFichier de calibration : {calibration_path}")
    print("\nAperçu des cinq mesures (valeurs entièrement fictives) :")
    print(records[["Timestamp", "Value_Temperature_2", "Value_Temperature_3", "Value_Pressure"]].to_string(index=False))
    print("\nCorrespondance prévue pour cet exemple :")
    print("  Value_Temperature_2  -> température externe")
    print("  Value_Temperature_3  -> température interne")
    print("  Value_Pressure       -> pression")
    print("\nAttention : ne réutilise pas cette calibration artificielle pour traiter des mesures réelles.")

    answer = input("\nPour lancer l'assistant et modifier le JSON local, tape DEMO : ").strip()
    if answer != "DEMO":
        print("Démonstration annulée. Aucun fichier n'a été modifié.")
        return 0

    try:
        result = configure_thermal(root)
    except (ValueError, FileNotFoundError) as exc:
        print(f"\nDémonstration interrompue : {exc}")
        return 1

    print("\n" + "=" * 72)
    print("DÉMONSTRATION TERMINÉE")
    print("=" * 72)
    print(f"Génération déclarée : {result['generation']}")
    print(f"Points associés : {', '.join(result['points'])}")
    print(f"Fichier de calibration : {result['table']}")
    print(f"Configuration modifiée : {lab_path}")
    print("Une copie de sauvegarde lab.json.bak-* a été créée dans le même dossier.")
    print("La calibration est maintenant déclarée dans le laboratoire local.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
