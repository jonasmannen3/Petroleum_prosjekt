"""Henting av live AIS-posisjon fra BarentsWatch, og kobling mot Sodir-riggnavn.

Krever egen klient-ID og hemmelighet, som du oppretter gratis på
barentswatch.no/minside (velg «AIS-client», ikke «API-client»). Se
developer.barentswatch.no/docs/appreg/ for stegene.

BarentsWatch har ikke en egen AIS-type for borerigger; AIS-kode 33 dekker
«Drill Ship / Drilling Jack Up / Drill Barge» under den offisielle
kategorien «Dredger», så den brukes bare som et hint, ikke et filter —
mange rigger sender uansett en annen eller ingen type. Matching skjer
derfor primært på navn.
"""
from __future__ import annotations

import re

import requests

TOKEN_URL = "https://id.barentswatch.no/connect/token"
LATEST_URL = "https://live.ais.barentswatch.no/v1/latest/combined"
TIMEOUT = (10, 60)
RIGG_SHIPTYPE_HINT = {33}  # Drill Ship / Drilling Jack Up / Drill Barge — kun en pekepinn, se modul-kommentar


class BarentsWatchFeil(Exception):
    """Feil fra BarentsWatch (feil nøkkel, nettverk, uventet svar)."""


def hent_token(klient_id: str, hemmelig: str) -> str:
    """Henter et access-token med client_credentials-flyten. Gyldig i ca. 1 time (expires_in i svaret)."""
    try:
        svar = requests.post(
            TOKEN_URL,
            data={"grant_type": "client_credentials", "client_id": klient_id,
                  "client_secret": hemmelig, "scope": "ais"},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=TIMEOUT,
        )
    except requests.RequestException as e:
        raise BarentsWatchFeil(f"Fikk ikke kontakt med BarentsWatch: {e}") from e
    if svar.status_code != 200:
        raise BarentsWatchFeil(
            f"BarentsWatch avviste pålogging (HTTP {svar.status_code}). Sjekk at klient-ID og hemmelighet er "
            "riktige, og at klienten er registrert som «AIS-client» på barentswatch.no/minside."
        )
    token = (svar.json() or {}).get("access_token")
    if not token:
        raise BarentsWatchFeil("BarentsWatch svarte uten access_token.")
    return token


def hent_siste_posisjoner(token: str) -> list[dict]:
    """Siste kjente posisjon for alle fartøy BarentsWatch har (vanligvis noen tusen). Vi filtrerer selv i Python,
    siden API-et ikke har noe «bare rigger»-filter vi kan stole på.

    Prøver først den utvidede («Full») modellen, som i tillegg gir destination/eta — dokumentert for
    strømme-endepunktet (/v1/combined?modelType=Full), ikke bekreftet for /v1/latest/combined spesifikt.
    Hvis BarentsWatch avviser parameterne her, faller vi tilbake til enkel modell i stedet for å feile
    hele kallet: destinasjon/ETA blir da bare utilgjengelig, AIS-posisjonen vises uansett."""
    forsok = ({"modelType": "Full", "modelFormat": "Json"}, None)
    siste_status = None
    for params in forsok:
        try:
            svar = requests.get(LATEST_URL, headers={"Authorization": f"Bearer {token}"}, params=params,
                                timeout=TIMEOUT)
        except requests.RequestException as e:
            raise BarentsWatchFeil(f"Fikk ikke kontakt med BarentsWatch: {e}") from e
        if svar.status_code == 401:
            raise BarentsWatchFeil("BarentsWatch avviste token (utløpt eller ugyldig).")
        if svar.status_code == 200:
            data = svar.json()
            return data if isinstance(data, list) else (data or {}).get("vessels") or []
        siste_status = svar.status_code
        # Full-modell ble avvist: prøv enkel modell (params=None) før vi gir opp.
    raise BarentsWatchFeil(f"BarentsWatch svarte med HTTP {siste_status}.")


def _normaliser(navn: str) -> str:
    """Gjør navn sammenlignbare: store bokstaver, bare bokstaver/tall/mellomrom, ett mellomrom mellom ord."""
    navn = re.sub(r"[^A-ZÆØÅ0-9 ]", " ", str(navn).upper())
    return re.sub(r"\s+", " ", navn).strip()


def _tall(rad: dict, *nokler: str) -> float | None:
    for k in nokler:
        v = rad.get(k)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
    return None


def normaliser_posisjon(rad: dict) -> dict:
    """Gjør en rå AIS-rad om til et fast sett felt. BarentsWatch sitt API har brukt litt ulike
    feltnavn i ulike versjoner/endepunkt (f.eks. latitude/lat, speedOverGround/sog), så vi
    sjekker flere alternativer i stedet for å anta ett bestemt skjema."""
    return {
        "navn": rad.get("name") or rad.get("shipName"),
        "mmsi": rad.get("mmsi"),
        "lat": _tall(rad, "latitude", "lat"),
        "lon": _tall(rad, "longitude", "lon", "long"),
        "fart": _tall(rad, "speedOverGround", "sog", "speed"),
        "kurs": _tall(rad, "courseOverGround", "cog", "course", "heading"),
        "tid": rad.get("msgtime") or rad.get("timestamp") or rad.get("time"),
        "match": rad.get("_match"),
        # Finnes bare når BarentsWatch ga oss "Full"-modellen (se hent_siste_posisjoner) — fritekst
        # skrevet av mannskapet selv, så ofte forkortet/utdatert/tom. Ikke en beregnet rute.
        "destinasjon": (str(rad.get("destination")).strip() or None) if rad.get("destination") else None,
        "eta": rad.get("eta") or None,
    }


def match_rigger(ais_liste: list[dict], riggnavn: list[str]) -> dict[str, dict]:
    """Kobler Sodir-riggnavn mot AIS-fartøy på navn. For hver rigg som finner en treff, returneres AIS-raden
    pluss et «match»-felt («eksakt» eller «delvis»), slik at du selv kan vurdere om koblingen er til å stole på.

    Rigger uten treff er ikke med i resultatet (appen viser det som «ingen AIS-treff», ikke som en feil)."""
    ais_med_navn = [(a, _normaliser(a.get("name") or "")) for a in ais_liste if a.get("name")]
    resultat: dict[str, dict] = {}
    for rigg in riggnavn:
        mål = _normaliser(rigg)
        if not mål:
            continue
        eksakt = next((a for a, n in ais_med_navn if n == mål), None)
        if eksakt:
            resultat[rigg] = {**eksakt, "_match": "eksakt"}
            continue
        # Delvis: riggnavnet inngår i AIS-navnet eller omvendt (f.eks. «DEEPSEA YANTAI» vs «DEEPSEA YANTAI DRILL»)
        delvise = [a for a, n in ais_med_navn if mål in n or n in mål]
        if len(delvise) == 1:
            resultat[rigg] = {**delvise[0], "_match": "delvis"}
        # 0 eller flere enn 1 delvis treff: for usikkert, rapporteres som "ingen treff" heller enn å gjette
    return resultat