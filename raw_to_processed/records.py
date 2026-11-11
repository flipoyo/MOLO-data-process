"""Lecture des fichiers RECORDS.csv (format brut de la carte SD, sans en-tête, séparateur ';').

Intégration de `records_to_excel.py` : `read_records` lit le fichier et le met sous forme de tableau
(une valeur par colonne) ; `convertir` reste disponible pour écrire le tableau en Excel (nécessite `openpyxl`,
installé seulement si on veut les fichiers .xlsx).

Colonnes : Device_ID ; Record ; Date (jj/mm/aaaa) ; Time (hh:mm:ss) ; Value_Pressure ; Value_Temperature_2 .. 6.
"""
from __future__ import annotations

import sys
import warnings
from pathlib import Path

import pandas as pd

COLONNES = [
    "Device_ID", "Record", "Date", "Time",
    "Value_Pressure",
    "Value_Temperature_2", "Value_Temperature_3", "Value_Temperature_4",
    "Value_Temperature_5", "Value_Temperature_6",
]
MAX_LIGNES_EXCEL = 1_048_575  # limite Excel (1 ligne réservée à l'en-tête)


def read_records(chemin_csv) -> pd.DataFrame:
    """Lit RECORDS.csv. Colonnes : COLONNES plus `Timestamp` (Date et Time réunis, NaT si illisibles).

    Les lignes à date ou heure illisibles sont gardées (valeurs NaT) et signalées par un avertissement.
    """
    # Le ';' final de chaque ligne crée une 11e colonne vide : on la supprime.
    df = pd.read_csv(chemin_csv, sep=";", header=None, skipinitialspace=True, dtype=str,
                     engine="python", skip_blank_lines=True)
    df = df.iloc[:, : len(COLONNES)]
    df.columns = COLONNES
    df = df.apply(lambda col: col.str.strip())

    dates = pd.to_datetime(df["Date"], format="%d/%m/%Y", errors="coerce")
    heures = pd.to_datetime(df["Time"], format="%H:%M:%S", errors="coerce")
    timestamp = dates + (heures - heures.dt.normalize())

    df["Record"] = pd.to_numeric(df["Record"], errors="coerce").astype("Int64")
    df["Date"] = dates.dt.date
    df["Time"] = heures.dt.time
    for c in COLONNES[4:]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["Timestamp"] = timestamp

    invalides = df[df["Timestamp"].isna()]
    if not invalides.empty:
        warnings.warn(f"{len(invalides)} ligne(s) avec date/heure invalide (index CSV : {list(invalides.index[:10])}...)")
    return df


def convertir(chemin_csv, chemin_xlsx=None):
    """RECORDS.csv -> fichier Excel (découpé en feuilles au-delà de la limite d'Excel). Nécessite openpyxl."""
    chemin_csv = Path(chemin_csv)
    chemin_xlsx = Path(chemin_xlsx) if chemin_xlsx else chemin_csv.with_suffix(".xlsx")
    df = read_records(chemin_csv).drop(columns="Timestamp")
    with pd.ExcelWriter(chemin_xlsx, engine="openpyxl", datetime_format="HH:MM:SS",
                        date_format="DD/MM/YYYY") as writer:
        for i, debut in enumerate(range(0, len(df), MAX_LIGNES_EXCEL), start=1):
            nom = "Records" if len(df) <= MAX_LIGNES_EXCEL else f"Records_{i}"
            df.iloc[debut: debut + MAX_LIGNES_EXCEL].to_excel(writer, sheet_name=nom, index=False)
            ws = writer.sheets[nom]
            for col in ws.columns:
                ws.column_dimensions[col[0].column_letter].width = 22 if col[0].column_letter == "A" else 16
    print(f"{len(df)} lignes converties -> {chemin_xlsx}")
    return df


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("Usage : python -m raw_to_processed.records RECORDS.csv [sortie.xlsx]")
    convertir(*sys.argv[1:3])
