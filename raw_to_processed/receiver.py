"""Le receiver : range les messages bruts de Toulouse dans raw_data/<sampling_point>/v2_lora/.

Règles :
  - un message est rattaché à un sampling point par l'identifiant d'appareil qu'il contient, via le
    laboratoire virtuel ;
  - la ligne reçue n'est jamais modifiée (le brut reste le brut) ;
  - un appareil inconnu va dans raw_data/_unrouted/, un message illisible dans raw_data/_invalid/ : rien n'est perdu ;
  - le fichier d'un jour est l'ENSEMBLE trié des messages de ce jour. Peu importe l'ordre ou le nombre de
    livraisons : le même ensemble de messages donne les mêmes octets, donc le même hash ;
  - le jour est celui du message, pas celui de l'horloge de la machine.

`ingest_messages` n'ouvre aucune connexion et se teste sans réseau. `listen` est la seule fonction qui parle
à un broker MQTT ; elle n'a pas été essayée contre un vrai serveur.
"""
from __future__ import annotations

import base64
import json
import struct
from pathlib import Path
from typing import Iterable

from .datarepo import RAW, SOURCES, raw_point_dir, refresh_ingest
from .lab import VirtualLab
from .operators import int_to_iso8601
from .readers import decode_sensor_data

UNROUTED = "_unrouted"
INVALID = "_invalid"


def parse_uplink(line: str) -> tuple[str, str]:
    """(identifiant d'appareil, date ISO du message) d'une ligne JSON. ValueError si illisible."""
    try:
        obj = json.loads(line)
        data = base64.b64decode(obj["data"], validate=True)
        ui, time_int, _ = decode_sensor_data(data)
        if not ui:
            raise ValueError("identifiant d'appareil absent")
        return ui, int_to_iso8601(time_int)
    except (KeyError, TypeError, ValueError, IndexError, struct.error, AttributeError) as exc:
        raise ValueError(f"message illisible : {exc}") from exc


def _sort_key(line: str):
    try:
        return parse_uplink(line)[1], line
    except ValueError:
        return "", line


def _merge_into(path: Path, new_lines: set[str]) -> int:
    """Ajoute les lignes nouvelles au fichier (trié, sans doublon exact). Retourne le nombre de lignes ajoutées."""
    existing = set()
    if path.exists():
        existing = {ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()}
    added = new_lines - existing
    if added:
        path.parent.mkdir(parents=True, exist_ok=True)
        merged = sorted(existing | new_lines, key=_sort_key)
        path.write_bytes(("\n".join(merged) + "\n").encode("utf-8"))
    return len(added)


def ingest_messages(data_root, lines: Iterable[str], lab: VirtualLab) -> dict:
    """Range des messages bruts. Retourne un compte rendu {routed, unrouted, invalid, duplicates}."""
    data_root = Path(data_root)
    targets: dict[Path, set[str]] = {}
    report = {"routed": {}, "unrouted": 0, "invalid": 0, "duplicates": 0}
    kinds: dict[Path, tuple[str, str | None]] = {}

    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        try:
            ui, iso = parse_uplink(line)
        except ValueError:
            path, kind = data_root / RAW / INVALID / "invalid.jsonl", ("invalid", None)
        else:
            point = lab.device_point(ui)
            if point is None:
                path, kind = data_root / RAW / UNROUTED / f"{iso[:10]}.jsonl", ("unrouted", None)
            else:
                path = raw_point_dir(data_root, point) / SOURCES["v2"] / f"{iso[:10]}.jsonl"
                kind = ("routed", point)
        targets.setdefault(path, set()).add(line)
        kinds[path] = kind

    touched_points = set()
    for path, new in sorted(targets.items()):
        added = _merge_into(path, new)
        report["duplicates"] += len(new) - added
        kind, point = kinds[path]
        if kind == "routed":
            if added:
                report["routed"][point] = report["routed"].get(point, 0) + added
                touched_points.add(point)
        else:
            report[kind] += added
    for point in sorted(touched_points):
        refresh_ingest(raw_point_dir(data_root, point))
    report["routed"] = dict(sorted(report["routed"].items()))
    return report


def ingest_file(data_root, jsonl_path, lab: VirtualLab) -> dict:
    """Rejoue un fichier de messages (par exemple ce que `collector.py` a stocké)."""
    with open(jsonl_path, encoding="utf-8") as fh:
        return ingest_messages(data_root, fh, lab)


def listen(config_path, data_root, lab: VirtualLab) -> None:  # pragma: no cover - nécessite un broker réel
    """Écoute le broker MQTT et range chaque message reçu. Bloquant. Non essayé contre un vrai serveur.

    Requiert `paho-mqtt` (extra `mqtt`). La configuration (adresse, identifiants, certificats) vit hors de git :
    voir config.example.json.
    """
    try:
        import paho.mqtt.client as mqtt
    except ImportError as exc:
        raise SystemExit("paho-mqtt est requis : pip install 'molonari-pipeline[mqtt]'") from exc
    cfg = json.loads(Path(config_path).read_text(encoding="utf-8"))
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    if cfg.get("username"):
        client.username_pw_set(cfg["username"], cfg.get("password") or None)
    if cfg.get("tls", True):
        client.tls_set(ca_certs=cfg.get("ca_file") or None, certfile=cfg.get("cert_file") or None,
                       keyfile=cfg.get("key_file") or None)

    def on_connect(client, userdata, flags, reason_code, properties=None):
        client.subscribe(cfg["topic"])

    def on_message(client, userdata, msg):
        report = ingest_messages(data_root, [msg.payload.decode("utf-8", errors="replace")], lab)
        print(report, flush=True)

    client.on_connect, client.on_message = on_connect, on_message
    client.connect(cfg["host"], int(cfg["port"]))
    client.loop_forever()
