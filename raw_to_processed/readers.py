"""Lecture des formats bruts : exports HOBO (V1) et messages LoRa Protobuf (V2).

Le codec Protobuf est écrit à la main (schéma `hardware/tests/Connection_test/sensor.proto`, proto2) :
    message SensorData { required string UI = 1; required int32 time = 2; repeated float measurements = 3; }
Il évite de dépendre de `sensor_pb2` / protobuf pour tester le pipeline.
"""
from __future__ import annotations

import base64
import csv
import json
import math
import struct
from pathlib import Path

import pandas as pd

from .operators import int_to_iso8601
from .states import RawV1, RawV2

NB_MEASUREMENTS = 6


# --------------------------------------------------------------------------- V1 : exports HOBO
def _read_hobo(path, names: list[str]) -> pd.DataFrame:
    """Ligne 1 = titre du tracé, ligne 2 = en-têtes, puis `index, date, valeurs..., colonnes vides`."""
    rows = []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.reader(fh)
        next(reader)
        next(reader)
        for row in reader:
            values = row[1:1 + len(names)]
            if len(values) < len(names) or any(v.strip() == "" for v in values):
                continue   # Molonaviz fait dropna() : on saute les lignes incomplètes
            rows.append([v.strip() for v in values])
    df = pd.DataFrame(rows, columns=names)
    for col in names[1:]:
        df[col] = df[col].astype(float)
    return df


def read_v1(raw_dir) -> RawV1:
    raw_dir = Path(raw_dir)
    pressure = _read_hobo(raw_dir / "P_measures.csv", ["Date", "Voltage", "Temp_Stream"])
    temperature = _read_hobo(raw_dir / "T_measures.csv", ["Date", "Temp1", "Temp2", "Temp3", "Temp4"])
    return RawV1(pressure=pressure, temperature=temperature)


# --------------------------------------------------------------------------- V2 : Protobuf
def _varint(n: int) -> bytes:
    out = bytearray()
    while True:
        byte = n & 0x7F
        n >>= 7
        out.append(byte | (0x80 if n else 0))
        if not n:
            return bytes(out)


def encode_sensor_data(ui: str, time_int: int, measurements) -> bytes:
    ui_bytes = ui.encode("utf-8")
    out = b"\x0a" + _varint(len(ui_bytes)) + ui_bytes + b"\x10" + _varint(time_int)
    for m in measurements:
        out += b"\x1d" + struct.pack("<f", m)      # champ 3, fixed32 (proto2 : non empaqueté)
    return out


def decode_sensor_data(data: bytes) -> tuple[str, int, list[float]]:
    ui, time_int, meas, i = "", 0, [], 0
    while i < len(data):
        tag, i = _read_varint(data, i)
        field, wire = tag >> 3, tag & 7
        if wire == 0:
            value, i = _read_varint(data, i)
            if field == 2:
                time_int = value
        elif wire == 2:
            length, i = _read_varint(data, i)
            chunk, i = data[i:i + length], i + length
            if field == 1:
                ui = chunk.decode("utf-8")
            elif field == 3:                        # variante empaquetée, par tolérance
                meas += list(struct.unpack(f"<{len(chunk) // 4}f", chunk))
        elif wire == 5:
            if field == 3:
                meas.append(struct.unpack("<f", data[i:i + 4])[0])
            i += 4
        else:
            raise ValueError(f"type de fil Protobuf non géré : {wire}")
    return ui, time_int, meas


def _read_varint(data: bytes, i: int) -> tuple[int, int]:
    shift = value = 0
    while True:
        byte = data[i]
        i += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, i
        shift += 7


def read_v2(raw_dir) -> RawV2:
    """Tous les `*.jsonl` du dossier (un message par ligne, ordre alphabétique des fichiers)."""
    files = sorted(Path(raw_dir).glob("*.jsonl"))
    if not files:
        raise FileNotFoundError(f"aucun fichier .jsonl dans {raw_dir}")
    rows = []
    for path in files:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                payload = json.loads(line)["data"]
                ui, time_int, meas = decode_sensor_data(base64.b64decode(payload))
                meas = (meas + [math.nan] * NB_MEASUREMENTS)[:NB_MEASUREMENTS]
                rows.append([ui, int_to_iso8601(time_int), *meas])
    cols = ["UI", "Date"] + [f"c{i}" for i in range(NB_MEASUREMENTS)]
    return RawV2(messages=pd.DataFrame(rows, columns=cols))
