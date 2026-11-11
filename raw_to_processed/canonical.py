"""Hash et formats canoniques : la même donnée doit toujours donner les mêmes octets, donc le même hash.

`canonical_json` et `digest` reproduisent ceux de ComplexGitSync (`MerkleRoot.canonical_json` / `digest`) ;
un test le vérifie quand ComplexGitSync est disponible.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path

import pandas as pd

from .states import READY_COLUMNS

DECIMALS = {"Temp1": 3, "Temp2": 3, "Temp3": 3, "Temp4": 3, "TempBed": 3, "Voltage": 5, "Pressure": 5}
DATE_FORMAT = "%Y-%m-%dT%H:%M:%SZ"          # heure UTC
NAIVE_DATE_FORMAT = "%Y-%m-%dT%H:%M:%S"     # fuseau inconnu : l'heure d'origine est conservée telle quelle

_FILE_TAG = b"MOLO:FILE:v1\x00"
_NODE_TAG = b"MOLO:NODE:v1\x00"


# --------------------------------------------------------------------------- JSON et fichiers
def canonical_json(payload) -> bytes:
    """Clés triées, sans espace, UTF-8 (même contrat que ComplexGitSync)."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def digest(payload) -> str:
    return hashlib.sha256(canonical_json(payload)).hexdigest()


def file_digest(path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def _subtree(digests: list[bytes]) -> bytes:
    """RFC 9162 §2.1.1 : arbre de Merkle sans duplication de feuille (comme ComplexGitSync)."""
    if not digests:
        return hashlib.sha256(b"").digest()
    if len(digests) == 1:
        return digests[0]
    split = 1
    while split * 2 < len(digests):
        split *= 2
    return hashlib.sha256(_NODE_TAG + _subtree(digests[:split]) + _subtree(digests[split:])).digest()


def list_files(root, suffixes=None, exclude=()) -> dict[str, str]:
    """{chemin relatif POSIX: sha256}, trié sur les octets UTF-8 du chemin."""
    root = Path(root)
    files = {}
    for p in root.rglob("*"):
        rel = p.relative_to(root).as_posix()
        if p.is_file() and rel not in exclude and (suffixes is None or p.suffix in suffixes):
            files[rel] = file_digest(p)
    return dict(sorted(files.items(), key=lambda kv: kv[0].encode("utf-8")))


def dir_digest(root, suffixes=None, exclude=()) -> str:
    """Racine de Merkle d'un dossier : une feuille par fichier (chemin + contenu)."""
    leaves = [hashlib.sha256(_FILE_TAG + canonical_json({"relative_path": rel, "sha256": sha})).digest()
              for rel, sha in list_files(root, suffixes, exclude).items()]
    return _subtree(leaves).hex()


def git_tree_hash(root) -> str | None:
    """Hash d'arbre git d'un dossier, sans créer de dépôt dedans et sans commit.

    C'est une racine de Merkle du contenu (blobs + chemins + modes) indépendante de la date, de l'auteur
    et de l'historique : deux exécutions identiques donnent le même hash, contrairement à un commit_sha.
    """
    root = Path(root).resolve()
    with tempfile.TemporaryDirectory() as tmp:
        env = {**os.environ, "GIT_DIR": tmp, "GIT_WORK_TREE": str(root),
               "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}
        try:
            subprocess.run(["git", "init", "-q"], env=env, cwd=root, check=True, capture_output=True)
            subprocess.run(["git", "-c", "core.autocrlf=false", "add", "-A", "-f", "."],
                           env=env, cwd=root, check=True, capture_output=True)
            out = subprocess.run(["git", "write-tree"], env=env, cwd=root, check=True, capture_output=True, text=True)
        except (OSError, subprocess.CalledProcessError):
            return None
    return out.stdout.strip()


# --------------------------------------------------------------------------- CSV canonique
def _fmt(value: float, decimals: int) -> str:
    if value != value:
        return ""
    s = f"{value:.{decimals}f}"
    return s[1:] if s.startswith("-") and float(s) == 0 else s      # jamais de « -0.000 »


def write_ready_csv(df: pd.DataFrame, path, utc: bool = True) -> None:
    """Lignes triées par date, colonnes dans un ordre fixe, décimales fixes, dates ISO, fins de ligne LF.

    `utc=True` : dates en UTC, suffixe Z. `utc=False` : fuseau d'origine inconnu, dates conservées sans suffixe.
    """
    df = df.sort_values("Date", kind="stable")
    dates = df["Date"].dt.strftime(DATE_FORMAT if utc else NAIVE_DATE_FORMAT).tolist()
    lines = [",".join(READY_COLUMNS)]
    for k, date in enumerate(dates):
        row = df.iloc[k]
        lines.append(",".join([date] + [_fmt(float(row[c]), DECIMALS[c]) for c in READY_COLUMNS[1:]]))
    Path(path).write_bytes(("\n".join(lines) + "\n").encode("utf-8"))
