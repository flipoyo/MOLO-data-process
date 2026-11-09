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
