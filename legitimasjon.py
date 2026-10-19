"""Valgfri lagring av BarentsWatch-nøkkel i en lokal JSON-fil, samme mønster som
favoritter.py.

OBS: nøkkelen lagres i klartekst i denne filen, på din egen maskin. Det er ikke
en passord-database — bare en måte å slippe å skrive inn klient-ID/hemmelighet
på nytt hver gang du starter appen. Lagring skjer bare når du selv trykker
«Lagre», aldri automatisk.
"""
from __future__ import annotations

import json
from pathlib import Path

FIL = Path(__file__).parent / "barentswatch_nokkel.json"


def last() -> dict[str, str]:
    """Leser lagret nøkkel fra disk. Returnerer tomme strenger hvis ingenting er lagret."""
    if not FIL.exists():
        return {"klient_id": "", "hemmelig": ""}
    try:
        data = json.loads(FIL.read_text(encoding="utf-8"))
        return {"klient_id": str(data.get("klient_id", "")), "hemmelig": str(data.get("hemmelig", ""))}
    except (json.JSONDecodeError, OSError):
        return {"klient_id": "", "hemmelig": ""}


def lagre(klient_id: str, hemmelig: str) -> bool:
    """Lagrer nøkkelen på disk. Returnerer False (uten å krasje) hvis mappen er skrivebeskyttet,
    f.eks. ved nettutplassering — da må nøkkelen skrives inn på nytt hver økt i stedet."""
    try:
        FIL.write_text(json.dumps({"klient_id": klient_id, "hemmelig": hemmelig}, ensure_ascii=False, indent=2),
                        encoding="utf-8")
        return True
    except OSError:
        return False


def slett() -> None:
    try:
        FIL.unlink(missing_ok=True)
    except OSError:
        pass