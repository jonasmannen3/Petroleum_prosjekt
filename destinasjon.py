"""Tolker AIS sin fritekst-destinasjon til en posisjon, slik at vi kan tegne en
rett linje (ikke en faktisk rute) fra en flyterigg til der den oppgir å skulle.

AIS-feltet «destination» skrives inn av mannskapet selv — det er ofte
forkortet, feilstavet, utdatert eller tomt. Ingen av de tre datakildene
(Sodir, BarentsWatch, Kystdatahuset) beregner en faktisk rute eller
sannsynlige mellomstopp, så dette er bevisst IKKE et forsøk på det — bare en
retningsvisende linje til et sted vi med rimelig sikkerhet kan gjenkjenne.
Finner vi ikke nøyaktig ett sikkert treff, returneres None, og appen viser
da bare rå tekst uten linje i stedet for å gjette.
"""
from __future__ import annotations

import re

import pandas as pd

# Store forsyningsbaser for norsk sokkel. Omtrentlige kai-/baseposisjoner — nok til en
# retningsvisende linje, IKKE til navigasjon.
FORSYNINGSBASER: dict[str, tuple[float, float]] = {
    "AGOTNES": (60.3831, 5.1575),           # CCB Ågotnes
    "CCB AGOTNES": (60.3831, 5.1575),
    "MONGSTAD": (60.8089, 5.0314),
    "DUSAVIK": (58.9893, 5.6897),            # Stavanger
    "KRISTIANSUND BASE": (63.1105, 7.7311),  # Vestbase
    "VESTBASE": (63.1105, 7.7311),
    "SANDNESSJOEN": (66.0217, 12.6308),      # Horvnes
    "HORVNES": (66.0217, 12.6308),
    "HAMMERFEST": (70.6634, 23.6821),        # Polarbase
    "POLARBASE": (70.6634, 23.6821),
    "FLORO": (61.5996, 5.0325),
    "COOP BASE AGOTNES": (60.3831, 5.1575),
}


def _normaliser(tekst: str) -> str:
    tekst = re.sub(r"[^A-ZÆØÅ0-9 ]", " ", str(tekst).upper())
    return re.sub(r"\s+", " ", tekst).strip()


def bygg_stedsoppslag(innretninger: pd.DataFrame, bronner: pd.DataFrame) -> dict[str, tuple[float, float]]:
    """Kjente navn -> (lat, lon), bygget i rekkefølge etter hvor sikker posisjonen er:
    1) forsyningsbaser (fast, nøyaktig), 2) innretninger fra Sodir (fast, nøyaktig),
    3) feltnavn (midtpunktet av brønnene i feltet — grovere, brukes bare hvis navnet
    ikke allerede finnes fra de sikrere kildene)."""
    steder: dict[str, tuple[float, float]] = {_normaliser(n): p for n, p in FORSYNINGSBASER.items()}
    if innretninger is not None and not innretninger.empty:
        for navn, lat, lon in innretninger[["navn", "lat", "lon"]].dropna().itertuples(index=False):
            steder.setdefault(_normaliser(navn), (float(lat), float(lon)))
    if bronner is not None and not bronner.empty and "felt" in bronner.columns:
        for felt, g in bronner.dropna(subset=["felt", "lat", "lon"]).groupby("felt"):
            steder.setdefault(_normaliser(felt), (float(g["lat"].mean()), float(g["lon"].mean())))
    return steder


def finn_posisjon(destinasjon_tekst, steder: dict[str, tuple[float, float]]) -> tuple[float, float] | None:
    """Eksakt treff først. Ellers: delvis treff (stedsnavnet inngår i teksten, eller omvendt)
    godtas bare når NØYAKTIG ett kjent sted passer — to eller flere mulige, eller ingen,
    gir None i stedet for en gjetning."""
    if destinasjon_tekst is None or not str(destinasjon_tekst).strip():
        return None
    mål = _normaliser(destinasjon_tekst)
    if len(mål) < 3:
        return None
    if mål in steder:
        return steder[mål]
    delvise = {navn: pos for navn, pos in steder.items() if len(navn) >= 3 and (navn in mål or mål in navn)}
    if len(delvise) == 1:
        return next(iter(delvise.values()))
    return None