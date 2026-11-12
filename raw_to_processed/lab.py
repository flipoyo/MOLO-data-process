"""Laboratoire virtuel : relie le matériel (appareils), les sampling points et les calibrations.

Les calibrations thermiques expérimentales sont référencées dans le laboratoire, mais leurs fichiers restent dans
<data_root>/calibration/thermal/ et ne sont jamais incorporés au JSON.

Le registre thermal_calibrations est indexé par génération matérielle. Un point peut sélectionner sa calibration
par hardware_generation, ou référencer explicitement une entrée avec thermal_calibration. Les anciens objets
thermiques directement placés dans un point restent acceptés pour compatibilité.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .canonical import digest
from .states import Calibration

LAB_PATH = Path("lab") / "lab.json"


@dataclass(frozen=True)
class VirtualLab:
    data: dict
    _device_map: dict = field(init=False, repr=False, compare=False, default_factory=dict)

    def __post_init__(self):
        calibrations = self.data.get("calibrations", {})
        thermal_calibrations = self.data.get("thermal_calibrations", {})
        points = self.data.get("sampling_points", {})
        if self.data.get("schema") != 1:
            raise ValueError("lab : schema 1 attendu")
        if not isinstance(thermal_calibrations, dict):
            raise ValueError("lab : 'thermal_calibrations' doit être un objet")
        device_map: dict[str, str] = {}
        for name, spec in points.items():
            if spec.get("calibration") not in calibrations:
                raise ValueError(f"lab : le point {name!r} cite une calibration inconnue {spec.get('calibration')!r}")
            ref = spec.get("thermal_calibration")
            if isinstance(ref, str) and ref not in thermal_calibrations:
                raise ValueError(f"lab : le point {name!r} cite une calibration thermique inconnue {ref!r}")
            if ref is not None and not isinstance(ref, (str, dict)):
                raise ValueError(f"lab : thermal_calibration de {name!r} doit être un identifiant ou un objet")
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
        path.write_text(json.dumps(self.data, sort_keys=True, indent=2, ensure_ascii=False) + "\\n", encoding="utf-8")

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

    def thermal(self, point: str) -> dict | None:
        """Renvoie la calibration thermique explicite ou celle de la génération matérielle."""
        point_spec = self.data["sampling_points"][point]
        ref = point_spec.get("thermal_calibration")
        registry = self.data.get("thermal_calibrations", {})
        if isinstance(ref, str):
            spec = registry[ref]
        elif isinstance(ref, dict):
            spec = ref
        else:
            generation = point_spec.get("hardware_generation")
            spec = registry.get(generation) if generation is not None else None

        if spec is not None:
            if not isinstance(spec, dict) or "table" not in spec:
                raise ValueError(f"lab : calibration thermique de {point!r} sans clé 'table'")
            if not spec["table"]:
                raise ValueError(f"lab : chemin de table thermique vide pour {point!r}")
        return spec

    def params(self, point: str, kind: str) -> dict:
        """Paramètres de lecture : ceux du point, puis ceux propres à la source ('v1' ou 'v2')."""
        spec = self.data["sampling_points"][point]
        return {**spec.get("params", {}), **spec.get(kind, {})}
