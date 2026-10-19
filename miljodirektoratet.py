"""Henting av petroleumsrelaterte høringer fra Miljødirektoratet — selve flyten (orkestrering).

Denne filen vokste seg etter hvert stor og rotete da nettverks-/Playwright-logikken og
regex-tolkingen lå side om side. Delt i tre for oversikt:
- miljodir_skrap.py: nettverkslaget (Playwright, henting av rå HTML) — vet ingenting om felt/
  brønn/rigg.
- miljodir_tolk.py: tolkingslaget (ren parsing/regex av allerede-hentet HTML) — ingen
  nettverkskall (unntatt et testhjelpemiddel), så dette kan testes helt uten nettleser.
- denne filen (miljodirektoratet.py): binder de to sammen til hent_relevante_horinger(), appens
  faktiske inngangspunkt. App.py og eksisterende tester trenger ikke endres — alt som før lå her
  er fortsatt tilgjengelig som miljodirektoratet.<navn> via re-eksportene under.
"""
from __future__ import annotations

from datetime import date, timedelta

from miljodir_skrap import (  # noqa: F401 — re-eksportert for app.py/tester, se docstring over
    ARKIV_URL,
    BASE,
    BRUKERAGENT,
    LISTE_URL,
    MiljodirektoratetFeil,
    _hent_alle_lenker,
    _nettleserokt,
    hent_arkiv_ra_html_for_feilsoking,
    hent_horingslenker,
    hent_ra_html_for_feilsoking,
    hent_side_med_nettleser,
    trekk_ut_lenker,
)
from miljodir_tolk import (  # noqa: F401 — re-eksportert for app.py/tester, se docstring over
    BORING_NOKKELORD,
    KJENTE_OPERATORER,
    TIMEOUT,
    _tolk_horingsside,
    er_relevant,
    hent_detaljer,
    requests,
)


def hent_relevante_horinger(
    kjente_felt: list[str] | None = None, maks: int = 15, dager_tilbake: int | None = 90,
    kun_vis_kjente_felt: bool = False,
) -> tuple[list[dict], list[dict], list[dict], dict]:
    """Full flyt, i ÉN delt nettleserøkt (se miljodir_skrap.py sin docstring for hvorfor): last
    høringslisten, filtrer på boring/brønn-relevans, naviger til og tolk hver matchende
    enkelthøringsside. Én høring som feiler (nettverk, uventet sidestruktur, fortsatt HTTP-feil)
    stopper ikke resten — den hoppes bare over, og telles opp i 'feilet'-lista i resultatet.

    `kjente_felt` testes mot teksten på hver enkelthøringsside for å gjenkjenne hvilket felt den
    gjelder (se miljodir_tolk._tolk_horingsside()) — appens «Høringer»-fane lar brukeren velge et
    ansvarsområde (et sett med felt/funn de selv følger) i stedet for automatisk å teste mot
    absolutt alle feltnavn i Sodir sitt register.

    `kun_vis_kjente_felt` (RETTET — tidligere fantes ikke dette: et valgt ansvarsområde merket bare
    treff med feltnavn NÅR det ble funnet, men ekskluderte aldri høringer som ikke gjaldt det
    feltet — i praksis kom ALLE boring/brønn-relaterte høringer med uansett hva brukeren valgte).
    Satt til True: bare høringer der det faktisk ble gjenkjent et felt SOM ER I «kjente_felt» tas
    med i «resultater» — altså et reelt, presist søk. Satt til False (standard, brukes når
    brukeren ikke har snevret inn ansvarsområdet i appen): all boring/brønn-relevans vises, som før.
    NÅR True: løkken under slutter å kutte kandidatlisten til «maks» FØR felt-filtreringen (se
    kommentaren ved «kandidater» under) — den ville ellers kunne gå tom for kandidater før den i
    det hele tatt rakk å teste de som faktisk gjaldt det valgte feltet.

    `dager_tilbake` (standard 90, altså ~3 måneder): lagt til etter at et ekte søk fant relevante
    høringer helt tilbake til 2022 — mer historikk enn det som er nyttig for et TIDLIG VARSEL om
    kommende brønner. Sendes videre som «eldst_publisert» til miljodir_skrap._hent_alle_lenker()
    (filtreres der i etterkant, se dens docstring for hvorfor — IKKE en antakelse om at API-et
    sorterer strengt kronologisk lenger, det viste seg feil i praksis). Sett til None for å IKKE
    begrense i tid (søk hele arkivet, som før).

    Returnerer (resultater, feilet, alle_lenker, paginering_diagnostikk).
    - «alle_lenker» er ALLE lenker som ble funnet på listesiden, FØR boring/brønn-filteret
      (er_relevant). Tas med som diagnostikk: er «alle_lenker» tom, klarte ikke Playwright å lese
      noe fra listesiden i det hele tatt (sidestruktur/selector-problem); er «alle_lenker»
      ikke-tom men «resultater» OG «feilet» begge tomme, fant den lenker men ingen av titlene
      matchet nøkkelordene i BORING_NOKKELORD (eller — når «kun_vis_kjente_felt» er True — ingen
      av dem gjaldt et felt i «kjente_felt»); er «feilet» ikke-tom, matchet filteret men selve
      enkelthøringssiden kunne ikke leses.
    - «paginering_diagnostikk» (ett flatt dict — se miljodir_skrap._hent_horinger_via_api() sin
      docstring for feltene: «sider_hentet», «stoppet», «feilmelding», «total_ifolge_api»,
      «kategori_id», «antall_for_tidsfiltrering») viser OM og HVORFOR henting fra Miljødirektoratets
      eget interne API (/hearingslistpage/gethearings — funnet ved å lese nettsidens egen
      JavaScript-bundle, ikke ved klikk-basert paginering lenger) faktisk kjørte."""
    with _nettleserokt() as side:
        # API-et er offset-paginert (parameteren «skip», økes med antall treff per side, ikke
        # sidenummer) og kan hente BÅDE åpne og avsluttede høringer i ett og samme kall
        # (searchExpired=true), så vi trenger ikke lenger klikke oss gjennom nummererte sider.
        # Et snevert ansvarsområde (maks > 15, satt av app.py sin «Høringer»-fane) betyr brukeren
        # bevisst leter etter noe spesifikt og er villig til å vente lenger — da henter vi flere
        # sider fra API-et (høyere maks_resultater) i stedet for å stoppe tidlig.
        maks_lenker = 600 if maks > 15 else 200
        eldst_publisert = date.today() - timedelta(days=dager_tilbake) if dager_tilbake is not None else None
        lenker, paginering_diagnostikk = _hent_alle_lenker(
            side, maks_resultater=maks_lenker, eldst_publisert=eldst_publisert
        )
        # RETTET: tidligere ble kandidatlisten kuttet til «maks» FØR felt-filtreringen under — det
        # betydde at hvis det valgte feltet (f.eks. «Nova») ikke var blant de «maks» første
        # generiske boring/brønn-treffene, ble den aldri engang undersøkt. Når brukeren har snevret
        # inn ansvarsområdet (kun_vis_kjente_felt=True), går vi i stedet gjennom ALLE boring/brønn-
        # relevante kandidater (uansett er allerede begrenset av «maks_lenker»/tidsgrensen over) og
        # stopper heller når vi har funnet «maks» treff PÅ DET VALGTE FELTET, se løkken under.
        kandidater = [l for l in lenker if er_relevant(l["tittel"])]
        if not kun_vis_kjente_felt:
            kandidater = kandidater[:maks]
        # Alle titler funnet på listesiden (åpne + avsluttede) — brukes i _tolk_horingsside() for å
        # luke ut «relaterte høringer»-bokser på detaljsidene, se miljodir_tolk.py sin docstring.
        alle_titler = [l["tittel"] for l in lenker]
        resultater, feilet = [], []
        for l in kandidater:
            try:
                html_detalj = hent_side_med_nettleser(side, l["url"])
                andre_titler = [t for t in alle_titler if t != l["tittel"]]
                tolket = _tolk_horingsside(html_detalj, l["url"], kjente_felt, andre_titler)
            except MiljodirektoratetFeil as e:
                feilet.append({"tittel": l["tittel"], "url": l["url"], "feil": str(e)})
                continue
            if kun_vis_kjente_felt and tolket["felt"] not in (kjente_felt or []):
                continue
            # «publisert» følger med fra lenke-nivået (se miljodir_skrap._hent_alle_lenker()) —
            # IKKE tolket av _tolk_horingsside() selv, siden den bare ser på enkelthøringssiden.
            # Kan være None (f.eks. trekk_ut_lenker()-fallbacken har ikke dette feltet).
            tolket["publisert"] = l.get("publisert")
            resultater.append(tolket)
            if len(resultater) >= maks:
                break
    return resultater, feilet, lenker, paginering_diagnostikk