"""Tolking (parsing-laget) av høringer fra Miljødirektoratet: tar allerede-hentet HTML (fra
miljodir_skrap.py) og trekker ut felt, brønn, rigg osv. med regex. Ingen nettverkskall her
(unntatt hent_detaljer() nederst, som er et testhjelpemiddel — se dens egen advarsel), så alt i
denne filen kan testes helt uten Playwright/nettleser."""
from __future__ import annotations

import re

import requests
from bs4 import BeautifulSoup

from miljodir_skrap import BRUKERAGENT, MiljodirektoratetFeil

TIMEOUT = (10, 30)

# Nøkkelord for å luke ut høringer som ikke gjelder boring/brønner på sokkelen — Miljødirektoratet
# publiserer høringer om alt fra avfall til kjemikalier, så dette er bevisst strengt. Juster listen
# selv hvis du vil fange opp mer (f.eks. legg til "seismikk" for undersøkelser, ikke bare boring).
BORING_NOKKELORD = [
    "boring", "brønn", "letebrønn", "produksjonsbrønn", "avgrensningsbrønn", "injeksjonsbrønn",
    "boreprogram", "boreinnretning", "kontinentalsokkelen", "havbunnsramme",
]

# Kjente operatører på norsk sokkel — brukes til å finne hvem en høring gjelder, siden
# formuleringen («X søker om ...») varierer for mye til å parses pålitelig på egen hånd.
KJENTE_OPERATORER = [
    "Equinor", "Aker BP", "Vår Energi", "Harbour Energy", "ConocoPhillips", "TotalEnergies",
    "Wintershall Dea", "OMV", "DNO", "Petoro", "Shell", "Repsol", "INEOS", "Neptune Energy",
    "Lundin", "OKEA", "Pandion Energy", "Source Energy", "Sval Energi", "PGNiG", "Spirit Energy",
]


def er_relevant(tittel: str) -> bool:
    """Grovfilter: inneholder tittelen noe som tyder på boring/brønn på sokkelen?
    Bevisst strengt — se BORING_NOKKELORD-kommentaren over."""
    t = tittel.lower()
    return any(ord_ in t for ord_ in BORING_NOKKELORD)


_BRONN_MONSTER = re.compile(r"\b\d{1,2}/\d{1,2}(?:-[A-ZÆØÅ0-9]+)+(?:\s[A-ZÆØÅ])?\b")
_DATO_LANG = re.compile(r"\b\d{1,2}\.\s*(?:januar|februar|mars|april|mai|juni|juli|august|"
                        r"september|oktober|november|desember)\s*\d{4}\b", re.I)
_DATO_KORT = re.compile(r"\b\d{1,2}\.\d{1,2}\.\d{4}\b")
_VARIGHET = re.compile(r"varighet[^.\d]{0,30}(\d+)\s*dager", re.I)
_SAKSNUMMER = re.compile(r"[Hh]øringsnummer\D{0,20}(\d{4}/\d+)")
_FRIST = re.compile(r"[Ff]rist\D{0,15}(\d{1,2}\.\d{1,2}\.\d{4})")
_BOREINNRETNING = re.compile(r"boreinnretning(?:en)?\s+([^.,;]{3,50})", re.I)
# NB: fanger selve datoen direkte (ikke "alt frem til neste punktum") — datoformatet
# "1. november 2026" har selv et punktum midt i seg, som ellers ville kuttet fangsten for tidlig.
_TIDLIGST = re.compile(
    r"[Tt]idligst forventet oppstart er\s+(\d{1,2}\.\s*(?:januar|februar|mars|april|mai|juni|juli|"
    r"august|september|oktober|november|desember)\s*\d{4}|\d{1,2}\.\d{1,2}\.\d{4})"
)


def _tolk_horingsside(
    html: str, url: str, kjente_felt: list[str] | None = None, andre_titler: list[str] | None = None
) -> dict:
    """Ren parsing, uten nettverkskall — testbar for seg selv. Feltuttrekket er regex basert på
    mønstre fra en ekte høringsside (Nova-eksempelet) — finner den ikke et felt, blir det bare
    None, ALDRI en gjetning. Teksten i «sammendrag» er alltid med, som sikkerhetsnett hvis
    regex-mønstrene ikke treffer akkurat denne høringens formulering.

    `andre_titler`: titlene på ALLE ANDRE høringer funnet på listesiden (se
    miljodirektoratet.hent_relevante_horinger). Grunnen: et ekte eksempel («Kristin og
    Tyrihans»-høringen) ble feilaktig tagget med feltet «Ormen Lange», selv etter at teksten ble
    begrenset til <main> — fordi siden (fortsatt inni <main>) også viser en «relaterte
    høringer»-boks med korte kort for ANDRE, urelaterte høringer (brukeren limte inn den eksakte
    teksten: «… søker om endring av tillatelse til produksjon, drift og vedlikehold på Ormen
    Lange Høring publisert 18.09.2026 Frist: 19.10.2026 …» — dette ER nettopp den andre høringens
    EGEN tittel, bare limt rett inn i teksten). Vi har ikke noen bekreftet CSS-selector å skille
    denne boksen fra selve artikkelen med, men vi vet allerede, FRA selve høringslisten, nøyaktig
    hvilken tekst som er «en annen hørings tittel» — så i stedet for å gjette på HTML-struktur,
    fjerner vi ganske enkelt de kjente, eksakte titlene på alle ANDRE høringer fra teksten før vi
    leter etter feltnavn i den."""
    suppe = BeautifulSoup(html, "html.parser")
    h1 = suppe.find("h1")
    tittel = h1.get_text(strip=True) if h1 else url
    # Begrenser oss til <main id="main"> (bekreftet fra en faktisk nedlastet side) for å luke ut
    # i hvert fall navigasjon/footer — faller tilbake til hele siden hvis <main> ikke finnes.
    innhold = suppe.find("main") or suppe
    # Normaliserer whitespace til enkle mellomrom: HTML-kildekoden kan ha linjeskift/innrykk inni
    # en og samme tekstblokk (f.eks. "... på\nOrmen Lange ...") som en nettleser ville vist som ett
    # mellomrom, men som BeautifulSoups get_text() ellers beholder rått. Uten dette kan en tittel
    # vi forsøker å fjerne (se under) feile å matche fordi mellomrommet i den egentlig er et
    # linjeskift i kildeteksten.
    tekst = re.sub(r"\s+", " ", innhold.get_text(" ", strip=True))

    # Fjerner enhver annen hørings egen tittel fra teksten (lengste først, så en lengre tittel som
    # inneholder en kortere ikke etterlater rester av den kortere) — se docstringen over.
    tekst_renset = tekst
    if andre_titler:
        titler_normalisert = {re.sub(r"\s+", " ", t).strip() for t in andre_titler if t}
        for annen_tittel in sorted(titler_normalisert - {tittel}, key=len, reverse=True):
            if annen_tittel in tekst_renset:
                tekst_renset = tekst_renset.replace(annen_tittel, " ")

    bronn_match = _BRONN_MONSTER.search(tekst_renset)
    varighet_match = _VARIGHET.search(tekst_renset)
    sak_match = _SAKSNUMMER.search(tekst_renset)
    frist_match = _FRIST.search(tekst_renset)
    rigg_match = _BOREINNRETNING.search(tekst_renset)
    tidligst_match = _TIDLIGST.search(tekst_renset)

    felt_navn = None
    felt_kontekst = None
    if kjente_felt:
        # Lengste treff vinner (f.eks. «Johan Sverdrup» foretrekkes over et kortere delnavn). Søker
        # i «tekst_renset» (andre høringers titler fjernet, se over), ikke i den rå «tekst».
        kandidater = []
        for f in kjente_felt:
            if not f or len(f) <= 2:
                continue
            m = re.search(rf"\b{re.escape(f)}\b", tekst_renset, re.I)
            if m:
                kandidater.append((f, m))
        if kandidater:
            felt_navn, felt_match = max(kandidater, key=lambda par: len(par[0]))
            start, slutt = felt_match.span()
            vindu_start = max(0, start - 120)
            vindu_slutt = min(len(tekst_renset), slutt + 120)
            felt_kontekst = (
                ("…" if vindu_start > 0 else "")
                + tekst_renset[vindu_start:start]
                + "⟦" + tekst_renset[start:slutt] + "⟧"
                + tekst_renset[slutt:vindu_slutt]
                + ("…" if vindu_slutt < len(tekst_renset) else "")
            )

    return {
        "tittel": tittel,
        "url": url,
        "bronn": bronn_match.group(0) if bronn_match else None,
        "operator": next((o for o in KJENTE_OPERATORER if o.lower() in tekst_renset.lower()), None),
        "rigg": rigg_match.group(1).strip() if rigg_match else None,
        "felt": felt_navn,
        # Viser EKSAKT hvor "felt" ble funnet i teksten (treffet markert med ⟦…⟧), med ~120 tegn
        # kontekst på hver side — til feilsøking når "felt" virker galt, se docstringen over.
        "felt_kontekst": felt_kontekst,
        "tidligst_oppstart": tidligst_match.group(1).strip() if tidligst_match else None,
        "varighet_dager": int(varighet_match.group(1)) if varighet_match else None,
        "saksnummer": sak_match.group(1) if sak_match else None,
        "frist": frist_match.group(1) if frist_match else None,
        "frist_utlopt": "er utløpt" in tekst_renset.lower() and "høringsfristen" in tekst_renset.lower(),
        "sammendrag": tekst_renset[:600],
    }


def hent_detaljer(
    url: str, kjente_felt: list[str] | None = None, andre_titler: list[str] | None = None
) -> dict:
    """Henter og tolker én høringsside med et rent `requests`-kall.

    ADVARSEL: bekreftet i praksis å få HTTP 403 fra Miljødirektoratet for enkelthøringssider
    (se miljodir_skrap.py sin docstring) — nettstedet ser ut til å blokkere vanlige,
    ikke-nettleser-baserte forespørsler. hent_relevante_horinger() (i miljodirektoratet.py)
    bruker DERFOR IKKE denne funksjonen; den bruker miljodir_skrap.hent_side_med_nettleser() i en
    delt Playwright-økt i stedet. Denne funksjonen står igjen fordi den er nyttig å teste
    parsing-logikken mot uten å måtte starte en nettleser — ikke fordi den er ment å brukes
    direkte mot det ekte nettstedet."""
    try:
        svar = requests.get(url, headers={"User-Agent": BRUKERAGENT}, timeout=TIMEOUT)
    except requests.RequestException as e:
        raise MiljodirektoratetFeil(f"Fikk ikke kontakt med Miljødirektoratet: {e}") from e
    if svar.status_code != 200:
        raise MiljodirektoratetFeil(f"Miljødirektoratet svarte med HTTP {svar.status_code} for {url}")
    return _tolk_horingsside(svar.text, url, kjente_felt, andre_titler)