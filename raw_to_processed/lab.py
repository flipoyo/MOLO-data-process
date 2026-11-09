"""Laboratoire virtuel : relie le matériel (appareils), les sampling points et la calibration.

C'est la table que lisent le receiver (à quel sampling point appartient ce message ?) et le pipeline
(quelle calibration, quels paramètres de lecture ?). C'est une donnée versionnée, pas du code.

Format du fichier `lab/lab.json` :

    {"schema": 1,
     "calibrations": {"P520_T001": {"beta": 3834.0, "v_ref": 3.3, "intercept": ..., "dudh": ..., "dudt": ...}},
     "sampling_points": {
        "Point034": {"calibration": "P520_T001",
                     "devices": ["VIRT-0001"],                      # identifiants lus dans les messages LoRa
                     "params": {"rules": {"Temp1": "iqr"}},         # facultatif, commun à V1 et V2
                     "v1": {"date_fmt": "%m/%d/%y %I:%M:%S %p", "utc_offset_hours": 0.0}}}}   # facultatif

La spec cible parle d'un `lab.yaml` dans MOLONARI-TWIN ; le prototype utilise du JSON pour ne pas ajouter de
dépendance. Le chemin du fichier se passe en paramètre (`--lab`) : il n'a pas à vivre dans le dépôt de données.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .canonical import digest
from .states import Calibration

LAB_PATH = Path("lab") / "lab.json"       # emplacement par défaut, relatif à la racine du dépôt de données


@dataclass(frozen=True)
class VirtualLab:
    data: dict
    _device_map: dict = field(init=False, repr=False, compare=False, default_factory=dict)

    def __post_init__(self):
        calibrations = self.data.get("calibrations", {})
        points = self.data.get("sampling_points", {})
        if self.data.get("schema") != 1:
            raise ValueError("lab : schema 1 attendu")
        device_map: dict[str, str] = {}
        for name, spec in points.items():
            if spec.get("calibration") not in calibrations:
                raise ValueError(f"lab : le point {name!r} cite une calibration inconnue {spec.get('calibration')!r}")
            for ui in spec.get("devices", []):
                if ui in device_map:
                    raise ValueError(f"lab : l'appareil {ui!r} est rattaché à {device_map[ui]!r} et à {name!r}")
                device_map[ui] = name
        object.__setattr__(self, "_device_map", device_map)

    @classmethod
    def load(cls, path) -> "VirtualLab":
        return cls(json.loads(Path(path).read_text(encoding="utf-8")))

    def save(self, path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.data, sort_keys=True, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    def digest(self) -> str:
        return digest(self.data)

    def points(self) -> list[str]:
        return sorted(self.data["sampling_points"])

    def has_point(self, point: str) -> bool:
        return point in self.data["sampling_points"]

    def device_point(self, ui: str) -> str | None:
        """Sampling point d'un appareil, ou None s'il est inconnu du laboratoire."""
        return self._device_map.get(ui)

    def devices(self, point: str) -> list[str]:
        return sorted(self.data["sampling_points"][point].get("devices", []))

    def calibration_name(self, point: str) -> str:
        return self.data["sampling_points"][point]["calibration"]

    def calibration(self, point: str) -> Calibration:
        return Calibration(**self.data["calibrations"][self.calibration_name(point)])

    def params(self, point: str, kind: str) -> dict:
        """Paramètres de lecture : ceux du point, puis ceux propres à la source ('v1' ou 'v2')."""
        spec = self.data["sampling_points"][point]
        return {**spec.get("params", {}), **spec.get(kind, {})}
