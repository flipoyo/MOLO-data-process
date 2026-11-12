"""Assistant interactif pour déclarer une calibration thermique dans lab/lab.json."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from .lab import LAB_PATH, VirtualLab
from .records import COLONNES
from .thermal import fit_calibration, load_calibration_table


def _ask(prompt, input_fn=input, default=None):
    suffix = f" [{default}]" if default is not None else ""
    value = input_fn(f"{prompt}{suffix} : ").strip()
    return value or (str(default) if default is not None else "")


def _ask_choice(prompt, choices, input_fn=input):
    options = "/".join(choices)
    while True:
        value = _ask(f"{prompt} ({options})", input_fn)
        if value in choices:
            return value
        print(f"Choix attendu : {options}")


def configure_thermal(data_root, lab_path=None, input_fn=input, print_fn=print) -> dict:
    """Configure une calibration thermique après lecture et ajustement préalables du fichier."""
    root = Path(data_root).resolve()
    config_path = Path(lab_path).resolve() if lab_path else root / LAB_PATH
    if not config_path.is_file():
        raise FileNotFoundError(f"Fichier laboratoire introuvable : {config_path}")
    data = json.loads(config_path.read_text(encoding="utf-8"))
    VirtualLab(data)  # valide la configuration existante avant toute modification

    print_fn("\n--- Déclaration d'une calibration thermique MOLONARI ---")
    generation = _ask("Identifiant de génération matérielle", input_fn)
    if not generation:
        raise ValueError("L'identifiant de génération ne peut pas être vide.")
    if generation in data.get("thermal_calibrations", {}):
        replace_existing = _ask_choice("Cette génération existe déjà. Remplacer sa déclaration ?", ["oui", "non"], input_fn)
        if replace_existing == "non":
            raise ValueError("Configuration annulée : génération déjà déclarée.")

    relative_table = _ask("Chemin du fichier depuis la racine du dépôt de données", input_fn)
    if not relative_table:
        raise ValueError("Le chemin du fichier est obligatoire.")
    table_path = (root / relative_table).resolve()
    try:
        relative_table = table_path.relative_to(root).as_posix()
    except ValueError as exc:
        raise ValueError("Le fichier de calibration doit se trouver dans le dépôt de données.") from exc
    if not table_path.is_file():
        raise FileNotFoundError(f"Fichier de calibration introuvable : {table_path}")

    file_format = _ask_choice("Format du fichier", ["records", "table"], input_fn)
    columns = None
    if file_format == "records":
        print_fn("Colonnes RECORDS disponibles : " + ", ".join(COLONNES))
        columns = {
            "external": _ask("Colonne de température EXTERNE", input_fn),
            "internal": _ask("Colonne de température INTERNE", input_fn),
            "pressure": _ask("Colonne de PRESSION", input_fn),
        }
        if any(value not in COLONNES for value in columns.values()):
            raise ValueError(f"Colonne inconnue. Colonnes autorisées : {', '.join(COLONNES)}")
        if len(set(columns.values())) != 3:
            raise ValueError("Les trois rôles doivent correspondre à trois colonnes distinctes.")
    else:
        preview = pd.read_excel(table_path) if table_path.suffix.lower() in {".xlsx", ".xls"} else pd.read_csv(table_path)
        print_fn("Colonnes détectées : " + ", ".join(map(str, preview.columns)))
        print_fn("Le fichier doit contenir exactement les rôles requis : Température externe, Température interne, Pression.")
    external_series = _ask("Nom de la série de température externe dans les données de terrain", input_fn, "TempBed")
    dt_text = _ask("Pas d'échantillonnage en secondes (laisser vide pour le lire dans Date)", input_fn)
    dt_seconds = float(dt_text) if dt_text else None
    if dt_seconds is not None and dt_seconds <= 0:
        raise ValueError("Le pas d'échantillonnage doit être strictement positif.")

    # Ne propose l'enregistrement que si le fichier est effectivement exploitable par le modèle.
    table = load_calibration_table(table_path, columns)
    fitted = fit_calibration(table, dt_seconds=dt_seconds, external_series=external_series)
    print_fn(f"Contrôle du fichier réussi : {len(table)} lignes, pas de calibration = {fitted.dt_seconds!r} s.")
    print_fn("Modèle thermique ajusté : " + json.dumps(fitted.thermal_model, ensure_ascii=False))
    print_fn("Modèle de pression ajusté : " + json.dumps(fitted.pressure_model, ensure_ascii=False))

    available_points = sorted(data.get("sampling_points", {}))
    print_fn("Sampling Points disponibles : " + (", ".join(available_points) or "(aucun)"))
    point_text = _ask("Sampling Points utilisant cette génération (séparés par des virgules)", input_fn)
    points = [p.strip() for p in point_text.split(",") if p.strip()]
    if not points:
        raise ValueError("Au moins un Sampling Point doit être associé à cette génération.")
    unknown = sorted(set(points) - set(available_points))
    if unknown:
        raise ValueError(f"Sampling Point(s) inconnu(s) : {', '.join(unknown)}")

    spec = {"table": relative_table, "external_series": external_series}
    if columns:
        spec["columns"] = columns
    if dt_seconds is not None:
        spec["dt_seconds"] = dt_seconds
    conflicts = [p for p in points if data["sampling_points"][p].get("hardware_generation") not in (None, generation)]
    if conflicts:
        answer = _ask_choice("Ces points sont déjà associés à une autre génération (" + ", ".join(conflicts) + "). Remplacer ?", ["oui", "non"], input_fn)
        if answer == "non":
            raise ValueError("Configuration annulée : associations existantes conservées.")

    data.setdefault("thermal_calibrations", {})[generation] = spec
    for point in points:
        data["sampling_points"][point]["hardware_generation"] = generation

    VirtualLab(data)  # vérification finale de cohérence
    print_fn("\nAperçu de la déclaration qui sera ajoutée à lab.json :")
    print_fn(json.dumps({"thermal_calibrations": {generation: spec},
                         "sampling_points": {p: {"hardware_generation": generation} for p in points}},
                        indent=2, ensure_ascii=False))
    confirm = _ask_choice("Enregistrer cette configuration ?", ["oui", "non"], input_fn)
    if confirm != "oui":
        raise ValueError("Configuration annulée : lab.json n'a pas été modifié.")

    config_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print_fn(f"Configuration enregistrée dans : {config_path}")
    return {"generation": generation, "points": points, "table": relative_table, "dt_seconds": fitted.dt_seconds}
