"""Henting (nettverkslaget) fra Miljødirektoratet: alt som faktisk snakker med
miljodirektoratet.no. Ren tolking av HTML-en som kommer tilbake ligger i miljodir_tolk.py i
stedet — denne filen vet ingenting om felt/brønn/rigg, bare om å få tak i rå HTML.

OPPDATERT ETTER EKTE TESTING: BÅDE høringslisten OG enkelthøringssidene hentes med Playwright
(en hodeløs, men ekte, nettleser), IKKE med `requests`. Grunnen er ikke (bare) at listesiden er
JavaScript-rendret — enkelthøringssidene selv er vanlig server-rendret HTML, men et rent
`requests`-kall mot dem ble avvist av Miljødirektoratet med HTTP 403 i praksis, mens den samme
siden lastes helt fint i en ekte nettleser. Mest sannsynlig et bot-vern (f.eks. noe som sjekker
nettleser-fingeravtrykk/headere) som en enkel Python-forespørsel ikke kommer forbi, selv med en
vanlig User-Agent-streng satt.

Løsningen: ÉN hodeløs nettleserøkt åpnes per kjøring av hent_relevante_horinger() (i
miljodirektoratet.py), og brukes til å navigere både til listesiden OG til hver enkelt
høringsside som matcher boring/brønn-filteret — se _nettleserokt() og hent_side_med_nettleser().

Historikk (til orientering, hvis noe fortsatt ikke stemmer):
- Første versjon prøvde å skrive i et søkefelt på listesiden for å la nettstedets egen
  filtrering gjøre jobben — det viste seg å være nettstedets GENERELLE søkefelt (samme på alle
  sider), som sendte hele siden bort til et urelatert søkeresultat. Fjernet; listesiden lastes
  nå rått, og er_relevant() (i miljodir_tolk.py) filtrerer på tittel-tekst i etterkant i stedet.
- Deretter viste et ekte prøvekjør at listesiden leses riktig, men et rent `requests`-kall mot
  enkelthøringssidene fikk HTTP 403 — derav denne delte-nettleserøkt-løsningen.
"""
from __future__ import annotations

import re
from contextlib import contextmanager
from datetime import date, datetime

from bs4 import BeautifulSoup

BASE = "https://www.miljodirektoratet.no"
LISTE_URL = f"{BASE}/hoeringer/"
# BEKREFTET RIKTIG: verifisert mot en ekte nedlastet kopi av denne siden (tittel "Avsluttede
# høringer fra Miljødirektoratet") — dette er altså IKKE en gjetning lenger. Samme nedlasting
# avklarte også den egentlige grunnen til at en kjent høring (Nova/35/9-X-1 H) aldri dukket opp:
# siden bruker ordentlig, NUMMERERT paginering (knapper 1–63, en egen "Neste side"-knapp med
# aria-label, ~630 høringer totalt à 10 per side) — IKKE scroll/"last mer", som tidligere versjoner
# av denne filen antok uten å ha sett den ekte HTML-en. Se _hent_sider_med_paginering() under for
# selve paginerings-logikken, og _NESTE_SIDE_VALGSER for den bekreftede CSS-selectoren.
ARKIV_URL = f"{BASE}/hoeringer/avsluttede-hoeringer/"
BRUKERAGENT = "Mozilla/5.0 (compatible; Sokkelradar/1.0; privat ikke-kommersielt verktøy)"

# OPPDATERT (etter at første versjon fant 0 lenker i praksis — strukturen var antatt, ikke
# bekreftet): matcher nå bredt på alt under /hoeringer/ med minst ett tegn etter, og lar
# er_relevant() (tittel-nøkkelord, i miljodir_tolk.py) gjøre presisjonsjobben etterpå i stedet
# for URL-formen. Et par åpenbare ikke-detaljsider (ren paginering/spørrestreng, eller rotsiden
# selv) luftes ut her.
_URL_MONSTER = re.compile(r"/hoeringer/[^\"'<>\s]+")
_IKKE_DETALJSIDE = re.compile(r"^/hoeringer/(avsluttede-hoeringer/?)?(\?.*)?$")


class MiljodirektoratetFeil(Exception):
    """Feil ved henting fra Miljødirektoratet (nettverk, uventet sidestruktur, Playwright mangler)."""


@contextmanager
def _nettleserokt():
    """Åpner én hodeløs Chromium-nettleser (Playwright) og gir deg en «side» (Page) å navigere
    med, lukket igjen automatisk når du går ut av 'with'-blokken. Delt av alt som snakker med
    miljodirektoratet.no i denne modulen, slik at listesiden og hver enkelthøringsside leses i
    SAMME nettleserøkt — se docstringen øverst i filen for hvorfor rent `requests` ikke duger
    her (HTTP 403)."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise MiljodirektoratetFeil(
            "Pakken «playwright» mangler. Installer med: pip install playwright, og deretter "
            "(én gang): playwright install chromium"
        ) from e
    try:
        with sync_playwright() as p:
            try:
                nettleser = p.chromium.launch(headless=True)
            except Exception as e:
                raise MiljodirektoratetFeil(
                    f"Fikk ikke startet Chromium ({e}). Har du kjørt «playwright install chromium»?"
                ) from e
            try:
                yield nettleser.new_page(user_agent=BRUKERAGENT)
            finally:
                nettleser.close()
    except MiljodirektoratetFeil:
        raise
    except Exception as e:
        raise MiljodirektoratetFeil(f"Uventet feil med nettleseren: {e}") from e


# BEKREFTET RIKTIG ROTÅRSAK (fra en ekte feilmelding i praksis, ikke en gjetning): paginering
# stoppet på side 1 med en Playwright-timeout som viste at et cookiebanner (Cookie Information,
# id="cookie-information-template-wrapper"/"coiOverlay") lå OVERPÅ «Neste side»-knappen og fanget
# opp hvert eneste klikk i 30 sekunder («subtree intercepts pointer events»). Knappen for å avvise
# banneret har denne eksakte ID-en i en ekte nedlastet arkivside (aria-label «Avvis alle»).
_COOKIE_AVVIS_VALGSER = "#declineButton"


def _lukk_cookiebanner(side) -> None:
    """Lukker Cookie Information-cookiebanneret («Avvis alle») hvis det vises, FØR noe forsøker å
    klikke på noe annet på siden. Dette er den faktiske fiksen for at _hent_sider_med_paginering()
    sin «Neste side»-klikk tidligere timet ut etter 30 sekunder med «subtree intercepts pointer
    events» i en ekte kjøring — se kommentaren ved _COOKIE_AVVIS_VALGSER. Finnes ikke banneret
    (allerede lukket, samtykke satt fra en tidligere side i samme nettleserøkt, eller nettstedet
    har endret seg), er ikke det en feil — da gjør denne rett og slett ingenting."""
    try:
        knapp = side.locator(_COOKIE_AVVIS_VALGSER).first
        if knapp.count() > 0:
            knapp.click(timeout=5_000)
            side.wait_for_timeout(300)  # liten pause så selve overlayen rekker å forsvinne fra DOM-en
    except Exception:
        pass


def _last_listeside(side, url: str = LISTE_URL) -> str:
    """Navigerer en allerede-åpen nettleserside til en høringsliste (åpne eller avsluttede, se
    LISTE_URL/ARKIV_URL) og returnerer HTML-en for FØRSTE side etter at JavaScript har kjørt —
    uten noe forsøk på å laste flere treff (se _hent_sider_med_paginering() for det, som er det
    hent_relevante_horinger() faktisk bruker). Denne funksjonen er nå bare til rå-HTML-
    feilsøkingsknappene i appen, der poenget er å se markup for én side, ikke å samle alt innhold.

    RETTET (viktig historikk): denne prøvde tidligere scroll og en gjettet «last mer»-knapp for å
    laste mer innhold — basert på en ren antakelse om at høringslisten brukte «uendelig scroll».
    En ekte nedlasting av arkivsiden (ARKIV_URL) viste at det er FEIL: siden bruker ordentlig,
    nummerert paginering (knapper merket 1, 2, 3 … opp til 63, med en egen «Neste side»-knapp),
    ikke scroll i det hele tatt. Scroll-forsøket gjorde dermed aldri noe — verken skadet eller
    hjalp — og er fjernet. Dette er nøyaktig hvorfor en kjent høring (Nova) forble usynlig uansett
    hvor mange scroll-runder som ble forsøkt: man kan ikke scrolle seg til side 9 av 63."""
    try:
        svar = side.goto(url, timeout=20_000, wait_until="networkidle")
        # RETTET: denne sjekket tidligere ALDRI responsstatusen (i motsetning til
        # hent_side_med_nettleser(), som alltid har gjort det for enkelthøringssider) — en feil
        # URL ville dermed feile HELT STILLE. Bekreftet nå at ARKIV_URL faktisk er riktig (en ekte
        # nedlastet arkivside viste tittelen «Avsluttede høringer fra Miljødirektoratet»), men
        # sjekken står igjen som en permanent beskyttelse mot at den slutter å stemme senere.
        if svar is not None and svar.status >= 400:
            raise MiljodirektoratetFeil(f"Miljødirektoratet svarte med HTTP {svar.status} for {url}")
        _lukk_cookiebanner(side)
        # Merk: skriver bevisst IKKE i noe søkefelt her — se feilsøkingsnotatet i toppen av filen
        # (første versjon traff nettstedets generelle søk og navigerte bort fra /hoeringer/).
        return side.content()
    except MiljodirektoratetFeil:
        raise
    except Exception as e:
        raise MiljodirektoratetFeil(f"Klarte ikke å laste høringslisten: {e}") from e


# BEKREFTET fra en ekte nedlastet arkivside (ikke lenger en gjetning): «Neste side»-knappen har
# nøyaktig dette aria-label-et, og får HTML-attributtet disabled="" når den er på siste side —
# se c_list-archive-pagination__button--next i den nedlastede HTML-en.
# IKKE LENGER I BRUK av _hent_alle_lenker() (se den funksjonens docstring lenger ned): etter at vi
# fant Miljødirektoratets eget interne API (_hent_horinger_via_api()), er klikk-basert paginering
# gjennom nummererte sider erstattet av API-kall. Koden under (_lukk_cookiebanner,
# _hent_sider_med_paginering) står igjen urørt — både fordi den dokumenterer ekte funnet
# feilsøkingsarbeid (cookiebanner-bugen, «ingen_knapp»-bugen) og fordi eksisterende tester fortsatt
# dekker den — men ingenting i den aktive kodeveien kaller den lenger.
_NESTE_SIDE_VALGSER = 'button[aria-label="Neste side"]'


def _hent_sider_med_paginering(
    side, url: str, maks_sider: int = 80, maks_lenker: int = 200
) -> tuple[list[dict], dict]:
    """Navigerer til `url` og klikker seg gjennom den EKTE, nummererte sidenavigasjonen
    (bekreftet fra en reell nedlastet arkivside — IKKE scroll/«uendelig scroll», som ble prøvd
    tidligere og aldri gjorde noe, siden den antakelsen var feil). Hver side ERSTATTER innholdet
    i DOM-en (det er ikke uendelig scroll), så lenkene trekkes ut og slås sammen for hver side,
    i stedet for å lese HTML-en én gang til slutt slik den gamle scroll-baserte løsningen gjorde.

    LAGT TIL (etter at appen i praksis fant ~37 lenker — altså omtrent «bare side 1 av begge
    listene», IKKE hundrevis som et fullt gjennomklikket 63-siders arkiv skulle gi): denne
    funksjonen returnerer nå OGSÅ en diagnostikk-dict ved siden av lenkene, fordi et stille
    `except Exception: break` tidligere gjorde det umulig å se OM og HVORFOR klikkingen stoppet
    tidlig — nøyaktig den typen stille feil som tidligere skjulte at scroll-koden aldri gjorde
    noe. Vi gjetter ikke lenger på hvorfor; vi returnerer fakta appen kan vise frem.

    Diagnostikk-feltene:
    - «sider_besokt»: antall sider lenker faktisk ble hentet fra (starter på 1).
    - «stoppet»: hvorfor løkken stoppet — "ingen_knapp" (fant ikke «Neste side»-knappen),
      "deaktivert" (knappen var der, men disabled — normalt siste side), "nok_lenker"
      (maks_lenker nådd), "maks_sider" (sikkerhetsgrensen nådd), "feil" (en exception under
      klikk/navigasjon — se «feilmelding»), eller "ingen_endring_etter_klikk" (knappen ble
      klikket, men innholdet på siden så likt ut selv etter 4 sekunders venting — tyder på at
      klikket ikke faktisk byttet side, f.eks. fordi selectoren traff feil element).
    - «feilmelding»: exception-teksten når «stoppet» er "feil", ellers None.

    Stopper ellers når: «Neste side»-knappen ikke finnes eller er deaktivert (siste side nådd),
    vi har samlet «maks_lenker» unike lenker, eller «maks_sider» sider er besøkt (sikkerhetsgrense
    — det ekte arkivet hadde 63 sider da dette ble bekreftet, så denne bør normalt ikke være
    begrensende)."""
    try:
        svar = side.goto(url, timeout=20_000, wait_until="networkidle")
        if svar is not None and svar.status >= 400:
            raise MiljodirektoratetFeil(f"Miljødirektoratet svarte med HTTP {svar.status} for {url}")
        # BEKREFTET ROTÅRSAK (fra en ekte feilmelding, ikke en gjetning): uten dette kallet fanger
        # cookiebanneret opp hvert klikk på «Neste side»-knappen i 30 sekunder før Playwright gir
        # opp — se _lukk_cookiebanner() sin docstring. Må skje FØR vi i det hele tatt prøver å
        # klikke noe som helst på siden.
        _lukk_cookiebanner(side)
        # Merk: skriver bevisst IKKE i noe søkefelt her — se feilsøkingsnotatet i toppen av filen.

        lenker: dict[str, str] = {}
        diagnostikk = {"sider_besokt": 0, "stoppet": None, "feilmelding": None}
        for i in range(maks_sider):
            for l in trekk_ut_lenker(side.content(), maks_lenker):
                if l["url"] not in lenker or len(l["tittel"]) > len(lenker[l["url"]]):
                    lenker[l["url"]] = l["tittel"]
            diagnostikk["sider_besokt"] = i + 1
            if len(lenker) >= maks_lenker:
                diagnostikk["stoppet"] = "nok_lenker"
                break
            try:
                knapp = side.locator(_NESTE_SIDE_VALGSER).first
                if knapp.count() == 0:
                    # LAGT TIL (etter at appen i praksis stoppet med «ingen_knapp» på side 2 av
                    # arkivet, der det ifølge den bekreftede 63-siders strukturen fortsatt skulle
                    # vært en «Neste side»-knapp): sjekk om teksten faktisk finnes et sted i den
                    # rå HTML-en selv om locator() ikke fant den som et klikkbart element. Finner
                    # vi teksten der, er dette mest sannsynlig en TIMING-feil (widgeten hadde ikke
                    # rukket å feste seg i DOM-en igjen etter sidebyttet før vi sjekket) — finner vi
                    # den IKKE noe sted, er det reelt borte (f.eks. en kortere liste enn antatt, eller
                    # at nettstedet strukturerer side 2+ annerledes enn side 1).
                    diagnostikk["stoppet"] = "ingen_knapp"
                    try:
                        diagnostikk["knapp_tekst_i_ra_html"] = "Neste side" in side.content()
                    except Exception:
                        diagnostikk["knapp_tekst_i_ra_html"] = None
                    break
                if knapp.is_disabled():
                    diagnostikk["stoppet"] = "deaktivert"
                    break
                innhold_for = side.content()
                knapp.click()
                # Siden er ikke en ny URL (knappen har ingen href — et JS-drevet bytte av
                # innhold), så vi venter til selve innholdet faktisk har endret seg i stedet for
                # en fast pause, med et tak slik vi ikke henger evig hvis klikket ikke gjorde noe.
                endret = False
                for _ in range(20):
                    side.wait_for_timeout(200)
                    if side.content() != innhold_for:
                        endret = True
                        break
                if endret:
                    # LAGT TIL: hovedinnholdet (høringskortene) kan ha endret seg idet vi oppdager
                    # det, men selve PAGINERINGS-widgeten (knappene) kan henge litt etter om den
                    # festes til DOM-en i et eget steg — gi den litt ekstra tid til å stabilisere
                    # seg FØR neste runde sjekker etter «Neste side»-knappen igjen, i stedet for å
                    # konkludere «ingen_knapp» på et øyeblikksbilde tatt for tidlig.
                    side.wait_for_timeout(500)
                if not endret:
                    # RETTET: tidligere ble dette tilfellet ikke oppdaget i det hele tatt — løkken
                    # gikk bare videre til neste runde og prøvde å klikke igjen på akkurat samme
                    # side, potensielt opptil «maks_sider» ganger uten noensinne å komme videre
                    # eller si ifra om det. Nå stopper vi og rapporterer det tydelig i stedet.
                    diagnostikk["stoppet"] = "ingen_endring_etter_klikk"
                    break
            except Exception as e:
                diagnostikk["stoppet"] = "feil"
                diagnostikk["feilmelding"] = str(e)
                break  # fant ikke knappen / klikk feilet -> anta at vi er ferdige, ikke krasj hele hentingen
        else:
            diagnostikk["stoppet"] = "maks_sider"

        return [{"tittel": t, "url": u} for u, t in lenker.items()], diagnostikk
    except MiljodirektoratetFeil:
        raise
    except Exception as e:
        raise MiljodirektoratetFeil(f"Klarte ikke å laste høringslisten (paginert): {e}") from e


def hent_side_med_nettleser(side, url: str) -> str:
    """Navigerer en allerede-åpen nettleserside til `url` og returnerer HTML-en. Brukes for
    enkelthøringssider, som avviser et rent `requests`-kall med HTTP 403 (se filens docstring)."""
    try:
        svar = side.goto(url, timeout=20_000, wait_until="networkidle")
    except Exception as e:
        raise MiljodirektoratetFeil(f"Fikk ikke lastet {url}: {e}") from e
    if svar is not None and svar.status >= 400:
        raise MiljodirektoratetFeil(f"Miljødirektoratet svarte med HTTP {svar.status} for {url}")
    return side.content()


def hent_ra_html_for_feilsoking() -> str:
    """Som hent_horingslenker(), men returnerer den rå HTML-en direkte i stedet for å prøve å
    tolke lenker ut av den. Til bruk når lenke-uttrekket finner 0 treff og den egentlige
    sidestrukturen må sees for å rettes — se diagnostikken i appens «Høringer»-fane.

    OBS: henter kun den ÅPNE høringslisten (LISTE_URL). Mistenker du noe med arkivet/de
    avsluttede høringene i stedet (f.eks. at en kjent høring aldri dukker opp, slik Nova gjorde
    ikke), bruk hent_arkiv_ra_html_for_feilsoking() i stedet — denne funksjonen har ALDRI vist oss
    den siden, og ARKIV_URL er en gjettet, ikke bekreftet, URL (se kommentaren ved ARKIV_URL)."""
    with _nettleserokt() as side:
        return _last_listeside(side, LISTE_URL)


def hent_arkiv_ra_html_for_feilsoking() -> str:
    """Som hent_ra_html_for_feilsoking(), men for den avsluttede/arkiverte høringslisten
    (ARKIV_URL) i stedet for den åpne. Lagt til fordi ARKIV_URL er en GJETTET URL vi aldri har
    bekreftet med ekte data, og fordi _last_listeside() (til nylig) ikke engang sjekket om
    navigasjonen dit faktisk lyktes — en feil her ville ha vært helt usynlig. Last ned denne og se
    etter: (1) om siden faktisk er høringsarkivet og ikke en 404/feilside, og (2) om den bruker
    scroll/"last mer" i det hele tatt, eller heller nummererte sider/en "neste side"-lenke, som
    denne modulen foreløpig ikke følger."""
    with _nettleserokt() as side:
        return _last_listeside(side, ARKIV_URL)


# BEKREFTET (ikke en gjetning): funnet ved å lese gjennom nettsidens egen JavaScript-bundle
# (archive.bundle.js) — selve nettsiden henter IKKE høringene ved å rendre HTML på serveren, den
# kaller dette interne API-et selv, med «skip» (offset, økning 20 per side), «subjectids»
# (kategori-ID), «searchTerm» og «searchExpired» (true = BÅDE åpne og avsluttede i ÉN liste).
_HOYRINGER_API_URL = "/hearingslistpage/gethearings"
# «Olje og gass»-kategoriens ID, lest ut av samme HTML (feltet «allCategories»). Snevrer søket
# til akkurat det som er relevant for petroleumsvirksomhet, i stedet for å måtte bla gjennom HELE
# nettstedets høringsarkiv (klima, avfall, kjemikalier, osv.) og luke ut irrelevante titler etterpå.
_KATEGORI_OLJE_OG_GASS = 6169

# ISO 8601 ("2026-10-01T00:00:00" e.l.) prøves først siden det er det vanligste formatet for
# .NET-baserte API-er (som dette ser ut til å være) — norsk kort-dato ("01.10.2026") som
# reserve, i tilfelle API-et faktisk serverer en allerede formatert tekststreng i stedet.
_KORT_DATO = re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})")


def _tolk_publisert_dato(verdi) -> date | None:
    """Tolker «published»-feltet fra API-et til en date — ALDRI en gjetning: finner den ingen av
    de to kjente formatene, returneres None, og den høringen blir da IKKE filtrert bort av en
    tidsgrense (se _hent_horinger_via_api()) — vi utelater bevisst ikke noe vi er usikre på."""
    if not verdi:
        return None
    s = str(verdi).strip()
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
    except ValueError:
        pass
    m = _KORT_DATO.match(s)
    if m:
        try:
            dag, mnd, ar = (int(x) for x in m.groups())
            return date(ar, mnd, dag)
        except ValueError:
            return None
    return None


def _hent_horinger_via_api(
    side, kategori_id: int | None = _KATEGORI_OLJE_OG_GASS, soke_avsluttede: bool = True,
    maks_treff: int = 600, maks_sider: int = 50, eldst_publisert: date | None = None,
) -> tuple[list[dict], dict]:
    """Henter høringer via nettstedets EGET interne API i stedet for å klikke seg gjennom
    paginerte HTML-sider. Et frittstående `requests`-kall mot dette API-et UTENFRA ble i praksis
    avvist av Cloudflare med en «Attention Required»-utfordringsside (bekreftet, ikke en
    gjetning) — derfor kalles det her INNENFRA den allerede åpne Playwright-siden
    (`side.evaluate(...)`, en fetch() kjørt i selve sidens kontekst, med nettleserens egne
    cookies), akkurat slik nettstedets egen React-kode gjør det. Browseren har allerede løst
    Cloudflare sin utfordring ved å laste en vanlig side normalt (se _hent_alle_lenker(), som
    navigerer dit FØR dette kalles).

    Mye enklere og mer robust enn den tidligere klikk-baserte paginering
    (_hent_sider_med_paginering, beholdt i denne filen men ikke lenger i bruk her): ingen knapper
    å finne/klikke, intet cookiebanner i veien, bare offset-basert paginering på strukturert JSON
    — og «searchExpired=true» gir BÅDE åpne og avsluttede høringer i én og samme liste, så vi
    trenger ikke lenger to separate kilder (LISTE_URL + ARKIV_URL) slått sammen etterpå.

    `eldst_publisert`: lagt til etter at et ekte søk i praksis fant relevante høringer helt
    tilbake til 2022 — mer enn det som trengs for et TIDLIG VARSEL om kommende brønner.

    RETTET (etter en ekte feil i praksis — «Siste 3 måneder» viste bare inneværende måned): denne
    antok FØRST at API-et returnerer ALT strengt sortert nyest-publisert-først, og stoppet derfor
    rått på første gamle treff. Det stemte ikke i praksis — mest sannsynlig fordi «searchExpired=
    true» limer sammen to i utgangspunktet separat sorterte lister (åpne + avsluttede høringer),
    ikke én sammenhengende kronologisk strøm, så en «gammel» post kan dukke opp midt mellom nyere.
    Filtreringen skjer derfor nå i ETTERKANT, etter at ALT innenfor «maks_treff»/«maks_sider» er
    hentet rått — tryggere, men betyr at tidsgrensen ikke lenger kutter ned hvor MANGE API-kall
    som gjøres (bare hvor mange av dem som vises). En høring der datoen ikke lot seg tolke (se
    _tolk_publisert_dato()) blir ALDRI filtrert bort på usikkert grunnlag — den tas alltid med.

    Returnerer (høringer, diagnostikk). Hver høring har Miljødirektoratets EGNE feltnavn — minst
    "title", "url" (relativ), "hearingNumber", "subjects" (liste med kategorinavn), "deadline",
    "published" — IKKE {"tittel", "url"} ennå; det oversettes i _hent_alle_lenker(). Diagnostikken:
    «sider_hentet» (antall API-kall), «stoppet» ("alle_hentet" = totalMatching nådd, "tom_side" =
    fikk 0 treff før det, "nok_treff" = maks_treff nådd, "maks_sider" = sikkerhetsgrensen nådd,
    "feil" = en feil under selve fetch-kallet, se «feilmelding»), «total_ifolge_api» (API-ets eget
    tall for totalt antall treff, FØR tidsfiltreringen), «kategori_id», «eldst_publisert»
    (ISO-dato-streng, eller None), «antall_for_tidsfiltrering» (hvor mange som ble hentet RÅTT,
    altså før «eldst_publisert» filtrerte noen bort — bare satt når «eldst_publisert» er gitt)."""
    rått: list[dict] = []
    diagnostikk = {
        "sider_hentet": 0, "stoppet": None, "feilmelding": None,
        "total_ifolge_api": None, "kategori_id": kategori_id,
        "eldst_publisert": eldst_publisert.isoformat() if eldst_publisert else None,
    }
    # Kjøres i SIDENS kontekst (ikke i Python), derfor JS-syntaks — en relativ URL løses mot
    # siden sin egen origin (miljodirektoratet.no), nøyaktig slik siden sin egen kode gjør det.
    skript = (
        "([url, params]) => fetch(url + '?' + new URLSearchParams(params).toString(), "
        "{headers: {'Accept': 'application/json'}}).then(r => r.ok ? r.json() "
        ": Promise.reject('HTTP ' + r.status))"
    )
    skip = 0
    for _ in range(maks_sider):
        params = {"skip": str(skip), "searchTerm": "", "searchExpired": "true" if soke_avsluttede else "false"}
        if kategori_id is not None:
            params["subjectids"] = str(kategori_id)
        try:
            data = side.evaluate(skript, [_HOYRINGER_API_URL, params])
        except Exception as e:
            diagnostikk["stoppet"] = "feil"
            diagnostikk["feilmelding"] = str(e)
            break
        diagnostikk["sider_hentet"] += 1
        treff = (data or {}).get("hearings") or []
        diagnostikk["total_ifolge_api"] = (data or {}).get("totalMatching")
        rått.extend(treff)

        if not treff:
            diagnostikk["stoppet"] = "tom_side"
            break
        if len(rått) >= maks_treff:
            diagnostikk["stoppet"] = "nok_treff"
            break
        skip += len(treff)
        total = diagnostikk["total_ifolge_api"]
        if total is not None and skip >= total:
            diagnostikk["stoppet"] = "alle_hentet"
            break
    else:
        diagnostikk["stoppet"] = "maks_sider"

    if eldst_publisert is None:
        return rått, diagnostikk

    diagnostikk["antall_for_tidsfiltrering"] = len(rått)
    innenfor_tidsgrense = [
        h for h in rått
        if (dato := _tolk_publisert_dato(h.get("published"))) is None or dato >= eldst_publisert
    ]
    return innenfor_tidsgrense, diagnostikk


def trekk_ut_lenker(html: str, maks_resultater: int = 200) -> list[dict]:
    """Ren parsing, uten Playwright — testbar for seg selv. Trekker ut {«tittel», «url»} fra
    allerede-rendret HTML. IKKE filtrert på boring/brønn ennå, se miljodir_tolk.er_relevant()."""
    lenker: dict[str, str] = {}  # url -> tittel, dedup automatisk
    suppe = BeautifulSoup(html, "html.parser")
    for a in suppe.find_all("a", href=True):
        href = a["href"]
        if not _URL_MONSTER.search(href) or _IKKE_DETALJSIDE.match(href):
            continue
        url = href if href.startswith("http") else f"{BASE}{href}"
        tittel = a.get_text(strip=True) or url
        if url not in lenker or len(tittel) > len(lenker[url]):
            lenker[url] = tittel
        if len(lenker) >= maks_resultater:
            break
    return [{"tittel": t, "url": u} for u, t in lenker.items()]


def _hent_alle_lenker(
    side, maks_resultater: int = 200, eldst_publisert: date | None = None
) -> tuple[list[dict], dict]:
    """Henter BÅDE åpne og avsluttede høringer i kategorien «Olje og gass» via nettstedets eget
    API (_hent_horinger_via_api) — se den funksjonens docstring for hvorfor dette erstattet den
    tidligere klikk-baserte paginering gjennom LISTE_URL/ARKIV_URL. Naviger FØRST til en vanlig
    side, slik at nettleseren faktisk har løst Cloudflare sin utfordring og har gyldige cookies
    før fetch-kallet — nøyaktig slik en ekte besøkende ville gjort det.

    `eldst_publisert` sendes rett videre til _hent_horinger_via_api() — se dens docstring.

    Returnerer ({"tittel", "url", "publisert"}-par, diagnostikk) — diagnostikken er nå ÉN dict (se
    _hent_horinger_via_api() sin docstring for feltene), ikke lenger delt per LISTE_URL/ARKIV_URL,
    siden det bare er ett API-kall-sett nå. «publisert» er API-ets rå «published»-streng (kan være
    None) — tas med videre fordi en FERSK høring som nevner en rigg er et sterkere «fortsatt
    aktiv»-signal enn Sodir sin ofte utdaterte planlagt-dato, se riggbekreftelse.py."""
    svar = side.goto(LISTE_URL, timeout=20_000, wait_until="networkidle")
    if svar is not None and svar.status >= 400:
        raise MiljodirektoratetFeil(f"Miljødirektoratet svarte med HTTP {svar.status} for {LISTE_URL}")
    treff, diagnostikk = _hent_horinger_via_api(
        side, maks_treff=maks_resultater, eldst_publisert=eldst_publisert
    )

    lenker: dict[str, tuple[str, str | None]] = {}
    for h in treff:
        rel_url = h.get("url") or ""
        full_url = rel_url if rel_url.startswith("http") else f"{BASE}{rel_url}"
        tittel = h.get("title") or full_url
        eksisterende = lenker.get(full_url)
        if eksisterende is None or len(tittel) > len(eksisterende[0]):
            lenker[full_url] = (tittel, h.get("published"))
    return (
        [{"tittel": t, "url": u, "publisert": p} for u, (t, p) in lenker.items()][:maks_resultater],
        diagnostikk,
    )


def hent_horingslenker(maks_resultater: int = 200, eldst_publisert: date | None = None) -> list[dict]:
    """Laster BÅDE den åpne og den avsluttede høringslisten via Miljødirektoratets eget interne
    API (se _hent_alle_lenker()/_hent_horinger_via_api() sine docstrings), kjørt inne i en hodeløs
    nettleser (Playwright) for å dele samme Cloudflare-cookies som en vanlig sidenavigering gir.
    Åpner sin egen nettleserøkt — hent_relevante_horinger()
    (i miljodirektoratet.py) gjør det samme selv, i en delt økt, for ikke å måtte åpne
    nettleseren på nytt for hver enkelthøringsside.

    Returnerer en liste med {"tittel", "url"} — IKKE filtrert på boring/brønn ennå, se
    miljodir_tolk.er_relevant(). Diagnostikken fra _hent_alle_lenker() kastes bevisst bort her for
    å holde denne enkle funksjonens kontrakt uendret (brukt slik i eksisterende tester/kode);
    hent_relevante_horinger() i miljodirektoratet.py er den som faktisk gir diagnostikken videre
    til appen."""
    with _nettleserokt() as side:
        lenker, _diagnostikk = _hent_alle_lenker(side, maks_resultater, eldst_publisert=eldst_publisert)
        return lenker