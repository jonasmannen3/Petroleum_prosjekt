"""Henting fra Sodir FactMaps (åpent API, ingen innlogging).

Denne filen gjør bare HTTP-kall og returnerer rå GeoJSON-features. Den vet
ingenting om kategorisering, tabeller eller kart — se databehandling.py og
posisjon.py for det. Skilt ut slik at henting kan testes og gjenbrukes for
seg (f.eks. i et eget varslingsskript) uten å dra med seg resten.
"""
from __future__ import annotations

import requests

BASE = "https://factmaps.sodir.no/api/rest/services/Factmaps/FactMapsWGS84/MapServer"
BRONN_LAG = 201      # "Wellbores, all"
INNR_LAG = 304       # "Facilities, in place"
SIDE = 1000          # MaxRecordCount hos Sodir
TIMEOUT = (10, 90)   # sekunder: tilkobling, lesing

# Sodir-felt -> kolonnenavn databehandling.py bruker videre
BRONN_FELT = {
    "wlbWellboreName": "navn",
    "wlbDrillingOperator": "operator",
    "wlbDrillingFacility": "rigg",
    "wlbFacilityTypeDrilling": "rigg_type",
    "wlbStatus": "status",
    "wlbPurpose": "formaal",
    "wlbPurposePlanned": "formaal_planlagt",
    "wlbField": "felt",
    "wlbDiscovery": "funn",
    "wlbMainArea": "omraade",
    "wlbProductionLicence": "lisens",
    "wlbEntryDate": "start",
    "wlbCompletionDate": "ferdig",
    "wlbEntryDatePlanned": "planlagt_start",
    "wlbDrillPerApprovedPermitDate": "tillatelse",
    "wlbWaterDepth": "vanndyp",
    "wlbTotalDepth": "total_dybde",
    "wlbFactPageUrl": "url",
    "wlbEntryYear": "start_ar",
}
INNR_FELT = {
    "fclName": "navn",
    "fclKind": "type",
    "fclFixedOrMoveable": "fast_eller_flyttbar",
    "fclPhase": "fase",
    "fclStatus": "status",
    "fclCurrentOperatorName": "operator",
    "fclWaterDepth": "vanndyp",
    "fclStartupDate": "oppstart",
    "fclFactPageUrl": "url",
}
INNR_KOLONNER = list(INNR_FELT.values()) + ["lon", "lat", "flyttbar"]


class SodirFeil(Exception):
    """Feil fra Sodir-API-et (ikke nettverksfeil)."""


def hent_lag(lag: int, where: str, felt: str) -> list[dict]:
    """Henter alle features fra et lag, side for side. Returnerer GeoJSON-features."""
    features: list[dict] = []
    offset = 0
    forrige: list[dict] | None = None
    while True:
        params = {
            "where": where,
            "outFields": felt,
            "outSR": "4326",
            "f": "geojson",
            "returnGeometry": "true",
            "orderByFields": "OBJECTID",
            "resultOffset": offset,
            "resultRecordCount": SIDE,
        }
        svar = requests.get(f"{BASE}/{lag}/query", params=params, timeout=TIMEOUT)
        svar.raise_for_status()
        data = svar.json()
        if "error" in data:
            raise SodirFeil(data["error"].get("message", "Feilmelding fra Sodir-API-et"))
        side = data.get("features") or []
        if side == forrige:   # serveren ignorerer resultOffset: unngå evig løkke
            break
        forrige = side
        features.extend(side)
        flagg = data.get("exceededTransferLimit") or (data.get("properties") or {}).get("exceededTransferLimit")
        if not (flagg or len(side) >= SIDE) or not side or len(features) > 50_000:
            break
        offset += len(side)
    return features


def hent_bronn_features(fra_ar: int) -> list[dict]:
    """Rå brønn-features: startet fra og med fra_ar, pluss alle uten startdato
    (tillatt, planlagt, aldri boret, osv. — databehandling.py kategoriserer dem)."""
    where = f"wlbEntryYear >= {int(fra_ar)} OR wlbEntryDate IS NULL"
    return hent_lag(BRONN_LAG, where, ",".join(BRONN_FELT))


def hent_innretning_features() -> list[dict]:
    """Rå innretnings-features: alt over vann som er på plass (faste og flyttbare)."""
    return hent_lag(INNR_LAG, "fclSurface = 'Y'", ",".join(INNR_FELT))