"""Enkel lagring av favorittrigger i en lokal JSON-fil.

Lagres på disk (ikke bare i nettleseren), slik at favorittene er der neste
gang du starter appen. Filen ligger i samme mappe som app.py.
"""
from __future__ import annotations

import json
from pathlib import Path

FIL = Path(__file__).parent / "favoritter.json"


def last() -> set[str]:
    if not FIL.exists():
        return set()
    try:
        return set(json.loads(FIL.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, OSError):
        return set()


def lagre(rigger: set[str]) -> None:
    try:
        FIL.write_text(json.dumps(sorted(rigger), ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass  # f.eks. skrivebeskyttet mappe ved nettutplassering: favoritter faller da tilbake til kun økten