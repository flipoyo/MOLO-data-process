"""Fabrique un faux dépôt de données (lab + brut V1 et V2) pour les tests et les démonstrations, sans réseau."""
from __future__ import annotations

import tempfile
from pathlib import Path

from .datarepo import import_v1
from .lab import LAB_PATH, VirtualLab
from .receiver import ingest_file
from .virtual_lab import DEFAULT_CALIBRATION, HOBO_DATE_FORMAT, LabConfig, make_counts, write_v1, write_v2


def build_data_repo(root, calib=DEFAULT_CALIBRATION, cfg: LabConfig = LabConfig(), unknown_messages: int = 5) -> dict:
    """Deux sampling points alimentés par la MÊME vérité synthétique :

    - PointV1 : fichiers HOBO déposés avec import_v1 ;
    - PointV2 : messages LoRa rangés par le receiver (plus quelques messages d'un appareil inconnu).
    """
    root = Path(root)
    lab = VirtualLab({
        "schema": 1,
        "calibrations": {"P520_T001": calib.to_dict()},
        "sampling_points": {
            "PointV1": {"calibration": "P520_T001", "devices": [],
                        "v1": {"date_fmt": HOBO_DATE_FORMAT, "utc_offset_hours": 0.0}},
            "PointV2": {"calibration": "P520_T001", "devices": [cfg.ui]},
        },
    })
    lab.save(root / LAB_PATH)
    counts = make_counts(cfg, calib)
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        write_v1(counts, calib, t / "v1")
        import_v1(root, "PointV1", t / "v1", lab)
        write_v2(counts, t / "v2", ui=cfg.ui)
        routed = ingest_file(root, t / "v2" / "uplinks.jsonl", lab)
        write_v2(counts.head(unknown_messages), t / "x", ui="UNKNOWN-9")
        unknown = ingest_file(root, t / "x" / "uplinks.jsonl", lab)
    return {"lab": lab, "counts": counts, "routed": routed, "unknown": unknown}


# --------------------------------------------------------------------------- calibration thermique synthétique
import numpy as np
import pandas as pd

THERMAL_TRUTH = {"a": 0.97, "b": 1.5, "g": 0.9}         # filtre « vrai » de l'expérience de calibration
PRESSURE_SENSITIVITY = {"k": -0.004, "p0": 0.30}        # pression = p0 + k * (Ti - 20) pendant la calibration
CALIBRATION_STEP_S = 60                                 # une mesure par minute


def make_calibration_records(path, n: int = 300, truth=THERMAL_TRUTH, sensitivity=PRESSURE_SENSITIVITY,
                             start: str = "2026-11-02 09:00:00") -> pd.DataFrame:
    """Écrit un RECORDS.csv synthétique (format de la carte SD) d'une expérience de calibration.

    Colonnes : Value_Temperature_2 = température externe (bain), Value_Temperature_3 = température interne du capteur,
    Value_Pressure = pression mesurée. Le rôle de chaque colonne est écrit ici et nulle part ailleurs : un vrai
    fichier doit être accompagné de sa correspondance (clé `columns` du laboratoire).
    Retourne la table (colonnes de calib.py) pour comparer avec ce que le calibrage retrouve.
    """
    t = np.arange(n)
    te = 20 + 6 * np.sin(2 * np.pi * t / 90) + 3 * np.sin(2 * np.pi * t / 37 + 1.0)
    ti = np.empty(n)
    ti[0] = truth["b"] + truth["g"] * te[0]
    for i in range(1, n):
        ti[i] = truth["a"] * ti[i - 1] + (1 - truth["a"]) * (truth["b"] + truth["g"] * te[i - 1])
    pressure = sensitivity["p0"] + sensitivity["k"] * (ti - 20)
    stamps = pd.date_range(start, periods=n, freq=f"{CALIBRATION_STEP_S}s")
    lines = [f"CAL-01; {i + 1}; {d.strftime('%d/%m/%Y')}; {d.strftime('%H:%M:%S')}; {pressure[i]:.7f}; {te[i]:.6f}; "
             f"{ti[i]:.6f}; 0.0; 0.0; 0.0;" for i, d in enumerate(stamps)]
    from pathlib import Path
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes(("\n".join(lines) + "\n").encode("utf-8"))
    return pd.DataFrame({"Date": stamps, "Température externe": te, "Température interne": ti, "Pression": pressure})


CALIBRATION_COLUMNS = {"external": "Value_Temperature_2", "internal": "Value_Temperature_3", "pressure": "Value_Pressure"}


def build_thermal_point(root, point: str = "PointThermique", n: int = 576, truth=THERMAL_TRUTH,
                        sensitivity=PRESSURE_SENSITIVITY) -> dict:
    """Ajoute à un dépôt de données un point dont la pression est faussée par la température du capteur.

    La charge vraie est connue ; la pression « mesurée » = charge vraie + dérive thermique (calculée avec le filtre
    de la calibration ramené au pas de 15 minutes). Une chaîne correcte retrouve la charge vraie.
    """
    from .datarepo import import_legacy_csv, load_lab, refresh_ingest
    from .lab import LAB_PATH, VirtualLab
    root = Path(root)
    lab_path = root / LAB_PATH
    lab_data = VirtualLab.load(lab_path).data if lab_path.exists() else {
        "schema": 1, "calibrations": {"P520_T001": DEFAULT_CALIBRATION.to_dict()}, "sampling_points": {}}
    lab_data.setdefault("calibrations", {}).setdefault("P520_T001", DEFAULT_CALIBRATION.to_dict())
    calib = DEFAULT_CALIBRATION

    dates = pd.date_range("2026-11-02 12:00:00", periods=n, freq="15min")
    hours = np.arange(n) * 0.25
    te = 14 + 3 * np.sin(2 * np.pi * hours / 24) + 0.4 * np.sin(2 * np.pi * hours / 5.3)
    a15 = truth["a"] ** (900 / CALIBRATION_STEP_S)
    ti = np.empty(n)
    ti[0] = truth["b"] + truth["g"] * te[0]
    for i in range(1, n):
        ti[i] = a15 * ti[i - 1] + (1 - a15) * (truth["b"] + truth["g"] * te[i - 1])
    # T_ref d'une calibration idéale = moyenne de la température interne pendant l'expérience (recalculée à l'identique)
    cal = make_calibration_records(root / "calibration" / point / "RECORDS.csv", truth=truth, sensitivity=sensitivity)
    t_ref = float(cal["Température interne"].mean())
    true_head = 0.12 + 0.05 * np.sin(2 * np.pi * hours / 72)
    measured = true_head + sensitivity["k"] * (ti - t_ref)
    volts = calib.intercept + calib.dudh * measured + np.round(te, 3) * calib.dudt

    fmt = "%d/%m/%Y %H:%M:%S"
    pressure = ["#,dates,pressure_differential_V,temperature_stream_C"]
    temperature = ["#,dates,temperature_depth_1_C,temperature_depth_2_C,temperature_depth_3_C,temperature_depth_4_C"]
    for i, d in enumerate(dates):
        pressure.append(f"{i + 1},{d.strftime(fmt)},{volts[i]:.7f},{te[i]:.3f}")
        temperature.append(f"{i + 1},{d.strftime(fmt)},{te[i] - 0.3:.3f},{te[i] - 0.5:.3f},{te[i] - 0.7:.3f},{te[i] - 0.9:.3f}")
    import tempfile
    lab_data["sampling_points"][point] = {
        "calibration": "P520_T001", "devices": [],
        "legacy_csv": {"date_fmt": fmt, "utc_offset_hours": None, "source_timezone": "unknown_naive_preserved"},
        "thermal_calibration": {"table": f"calibration/{point}/RECORDS.csv", "columns": CALIBRATION_COLUMNS,
                                "external_series": "TempBed"}}
    lab = VirtualLab(lab_data)
    lab.save(lab_path)
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        (t / "p.csv").write_text("\n".join(pressure) + "\n", encoding="utf-8")
        (t / "t.csv").write_text("\n".join(temperature) + "\n", encoding="utf-8")
        import_legacy_csv(root, point, t / "p.csv", t / "t.csv", lab)
    return {"lab": lab, "dates": dates, "true_head": true_head, "measured": measured, "t_ref": t_ref}


# --------------------------------------------------------------------------- faux dataAnalysis
def build_dataanalysis_mini(src) -> None:
    """Petit dossier à la manière de MOLONARI1D/dataAnalysis : trois points aux formats différents.

    Point 7  : export HOBO américain (en-tête GMT+01:00, 12 h AM/PM), capteur P606, fichiers aux horodatages décalés.
    Point 8  : format « legacy » sans fuseau, mêmes horodatages dans les deux fichiers, capteur P506 (fichier à `;`).
    Point 9  : capteur inconnu de la configuration : doit être signalé, pas importé.
    """
    src = Path(src)
    conf = src / "configuration"
    for sub in ("pressure_sensors", "shafts", "temperature_sensors"):
        (conf / sub).mkdir(parents=True, exist_ok=True)
    (conf / "pressure_sensors" / "P606.csv").write_text(
        "Sensor_Name,P606\nDatalogger,Rasberry PI\nCalibration_Date,2018/01/02 10:10:10\nIntercept,1.27109693909151\n"
        "dU/dH,1.01010102487901\ndU/dT,0.00220946701100414\nSigma_Meas_P,0.0437474461778472", encoding="utf-8")
    (conf / "pressure_sensors" / "P506.csv").write_bytes(
        b"Intercept;1.46247546343116\r\ndU/dH;5.29564291838726\r\ndU/dT;0.0040301365342419\r\n")
    (conf / "shafts" / "S001.csv").write_text("Shaft_Name,S001\n", encoding="utf-8")
    (conf / "temperature_sensors" / "T001.csv").write_text("Sensor_Name,T001\n", encoding="utf-8")

    def point(folder, number, sensor, pressure_name, temperature_name, pressure_text, temperature_text, cleaned=True):
        d = src / "raw_data" / folder
        d.mkdir(parents=True, exist_ok=True)
        (d / pressure_name).write_bytes(pressure_text.encode("utf-8"))
        (d / temperature_name).write_bytes(temperature_text.encode("utf-8"))
        (d / "geometrie.txt").write_text(f"#pressure_sensor\n{sensor}\n#depth river bed - river tube [cm]\n\n"
                                         "#temperature_depths[cm]\n10;20;30;40", encoding="utf-8")
        (d / f"point{number}_info.txt").write_text(f"Point_Name,Point{number}\nP_Sensor_Name,{sensor}\n", encoding="utf-8")
        if cleaned:
            (d / f"point{number}_pression_cleaned.csv").write_text(
                "dates,temperature_stream,dH\n" + "".join(f"{r}\n" for r in cleaned_p[number]), encoding="utf-8")
            (d / f"point{number}_temperature_cleaned.csv").write_text(
                "dates,Temp_profondeur_10,Temp_profondeur_20,Temp_profondeur_30,Temp_profondeur_40\n"
                + "".join(f"{r}\n" for r in cleaned_t[number]), encoding="utf-8")

    # Point 7 : 06/27/16 12:00 AM/PM, GMT+01:00 ; pression 3 lignes, températures 4 lignes (une de plus au début)
    hobo_p = ('\ufeffTitre de tracé : p7,,,,\n#,"Date Heure, GMT+01:00","Tension, V (LGR S/N: 1)","Temp., °C (LGR S/N: 1)",,\n'
              "1,06/27/16 12:00:00 PM,1.26787,18.342,,\n2,06/27/16 12:15:00 PM,1.26000,18.400,,\n3,06/27/16 12:30:00 PM,1.25500,18.450,,\n")
    hobo_t = ('\ufeff"Titre de tracé : t7"\n"#","Date Heure, GMT+01:00","Temp., °C (LGR S/N: 2)","Temp., °C (LGR S/N: 2)",'
              '"Temp., °C (LGR S/N: 2)","Temp., °C (LGR S/N: 2)"\n'
              "1,06/27/16 11:45:00 AM,13.800,13.800,13.800,13.800\n2,06/27/16 12:00:00 PM,13.810,13.834,13.858,13.810\n"
              "3,06/27/16 12:15:00 PM,13.820,13.840,13.860,13.820\n4,06/27/16 12:30:00 PM,13.830,13.850,13.870,13.830\n")
    # Point 8 : jj/mm/aaaa hh:mm:ss sans fuseau
    leg_p = ("#,dates,pressure_differential_V,temperature_stream_C\n1,14/04/2018 13:45:00,1.01682,13.778\n"
             "2,14/04/2018 14:00:00,1.04383,13.660\n")
    leg_t = ("#,dates,temperature_depth_1_C,temperature_depth_2_C,temperature_depth_3_C,temperature_depth_4_C\n"
             "1,14/04/2018 13:45:00,13.124,12.467,12.022,11.821\n2,14/04/2018 14:00:00,13.081,12.388,11.910,11.686\n")
    def head(u, t):                      # formule de Molonaviz, recalculée ici avec les constantes de P506.csv
        return (u - t * 0.0040301365342419 - 1.46247546343116) / 5.29564291838726

    cleaned_p = {"7": [], "8": [f"14-04-2018 13:45,13.778,{head(1.01682, 13.778)!r}",
                                f"14-04-2018 14:00,13.66,{head(1.04383, 13.66)!r}"]}
    cleaned_t = {"7": [], "8": ["14-04-2018 13:45,13.124,12.467,12.022,11.821", "14-04-2018 14:00,13.081,12.388,11.91,11.686"]}
    point("point7_27_06_16", "7", "p606", "p7.csv", "t7.csv", hobo_p, hobo_t, cleaned=False)
    point("point8_14_04_18", "8", "p506", "p8.csv", "t8.csv", leg_p, leg_t)
    point("point9_01_01_20", "9", "p999", "p9.csv", "t9.csv", leg_p, leg_t, cleaned=False)
