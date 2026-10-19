"""UI for «🔭 Høringer (tidlig varsel)»-fanen — skilt ut fra app.py (som ellers vokste seg stor)
for oversikt. Importeres og kalles fra app.py sin «with fane5:»-blokk via vis_fane(); har ingen
egen tilstand utover det som sendes inn og det som ligger i st.session_state (delt med app.py).
"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import streamlit as st
from streamlit_folium import st_folium

import destinasjon as dest
import favoritter
import geoavstand
import miljodirektoratet as md
import posisjon as pos
import tidslinje as tid

STOPP_FORKLARING = {
    "tom_side": "nådde slutten av listen (ingen flere høringer)",
    "nok_treff": "hentet nok til grensen som var satt over",
    "alle_hentet": "hentet alt API-et selv oppga at det hadde",
    "maks_sider": "sikkerhetsgrensen for antall sider ble nådd",
    "feil": "en feil oppstod underveis — se feilmeldingen under",
    None: "(ikke fullført)",
}

HORING_TIDSGRENSE_ALTERNATIVER = {
    "Siste 3 måneder": 90, "Siste 6 måneder": 182, "Siste år": 365, "Alt (ingen grense)": None,
}


@st.cache_data(ttl=30 * 60, show_spinner=False)
def _last_horinger(kjente_felt: tuple[str, ...], maks: int, dager_tilbake: int | None, kun_vis_kjente_felt: bool):
    """Tidlig-varsel-søk mot Miljødirektoratets høringer (se miljodirektoratet.py). Bufres på
    samme måte som Sodir-dataene i app.py — en «Oppdater data nå» der tømmer HELE
    st.cache_data-registeret (felles på tvers av filer), så også dette bufferet."""
    return md.hent_relevante_horinger(list(kjente_felt), maks, dager_tilbake, kun_vis_kjente_felt)


def _felt_sikkerhet(r: dict) -> tuple[str, str]:
    """Konfidensindikator: et enkelt, forklarlig mål på hvor sikker feltgjenkjenningen er — IKKE
    statistikk, bare et par synlige regler, så du selv kan vurdere om du bør dobbeltsjekke:
    - "grå": feltet ble ikke gjenkjent i det hele tatt.
    - "gul": feltet ble funnet, men bare ÉN gang i den undersøkte teksten, og INGEN av de andre
      feltene (brønn/rigg/operatør) ble gjenkjent i samme høring — kan være en tilfeldig
      forekomst et sted i teksten.
    - "grønn": feltet ble enten nevnt FLERE ganger, ELLER minst ett av brønn/rigg/operatør også
      ble gjenkjent — flere uavhengige signaler som peker samme vei.
    Dette endrer ALDRI hva som vises, bare hvor mye du bør stole på det uten å sjekke
    «sammendrag»/kontekst-utdraget selv."""
    if not r.get("felt"):
        return "grå", "Felt ikke gjenkjent automatisk."
    # NB: bruker KUN «sammendrag» her, ikke «felt_kontekst» i tillegg — felt_kontekst er bare et
    # utdrag AV sammendraget (samme tekst, markert rundt treffet), så å telle begge ville dobbelt-
    # telt den samme, ene forekomsten og feilaktig vist "grønn" for noe som bare er nevnt én gang.
    tekst = (r.get("sammendrag") or "").lower()
    antall = tekst.count(r["felt"].lower())
    har_annen_stotte = bool(r.get("bronn") or r.get("rigg") or r.get("operator"))
    if antall >= 2 or har_annen_stotte:
        return "grønn", "Flere signaler i teksten støtter dette feltet."
    return "gul", "Feltet nevnt kun én gang i teksten — dobbeltsjekk selv før du stoler på det."


def _match_kjent_rigg(uttrukket: str, alle_rigger: list[str]) -> tuple[str, bool]:
    """Riggnavnet regex fant i høringsteksten (r['rigg']) kan ha en litt annen skrivemåte enn
    Sodir sitt offisielle navn (f.eks. ekstra ord fanget opp av mønsteret) — favorittlisten MÅ
    bruke samme stavemåte som Sodir-dataene for at matchingen andre steder i appen (kart, tabeller)
    skal fungere. Prøver først eksakt (case-insensitiv) treff, så et løst delstreng-treff mot
    `alle_rigger` (ekte riggnavn fra Sodir). Finner den ikke noe tydelig, returneres det rå
    uttrekket uendret — ALDRI en gjetning på et navn som ikke finnes i dataene.

    Returnerer (navn å bruke, om det var et bekreftet Sodir-treff)."""
    u = uttrukket.strip().rstrip(".,;:")
    for kjent in alle_rigger:
        if kjent.lower() == u.lower():
            return kjent, True
    for kjent in alle_rigger:
        if kjent.lower() in u.lower() or u.lower() in kjent.lower():
            return kjent, True
    return u, False


def _estimert_completion_oppstart(r: dict) -> date | None:
    """Grovt estimat på når completion (lower completion) tidligst kan starte: boringens oppgitte
    «tidligst forventet oppstart» PLUSS den oppgitte varigheten («varighet_dager») — altså antatt
    ferdig boret-dato, IKKE en bekreftet completion-dato (som verken høringen eller Sodir sier noe
    om). Returnerer None (ALDRI en gjetning) hvis en av de to tallene mangler, eller hvis
    oppstartsdatoen ikke lot seg tolke (se tidslinje.tolk_dato_tekst)."""
    if not r.get("tidligst_oppstart") or not r.get("varighet_dager"):
        return None
    oppstart = tid.tolk_dato_tekst(r["tidligst_oppstart"])
    if oppstart is None:
        return None
    return oppstart + timedelta(days=r["varighet_dager"])


def _eksporter_csv(resultater: list[dict], alle_rigger: list[str]) -> str:
    """Bygger CSV-teksten for nedlastingsknappen — ren funksjon, ingen Streamlit-kall, så den kan
    testes for seg selv. «Favorittrigg»-kolonnen leses fra st.session_state (samme sted appen
    ellers holder favorittene), slik at du i Excel kan sortere/filtrere på akkurat det. Riggnavnet
    normaliseres mot Sodir sitt register (se _match_kjent_rigg()) FØR det skrives ut, samme som i
    selve visningen og favoritt-knappen — konsistent stavemåte i hele appen."""
    favoritter_nå = st.session_state.get("favoritter", set())
    rader = []
    for r in resultater:
        navn = None
        if r.get("rigg"):
            navn, _ = _match_kjent_rigg(r["rigg"], alle_rigger)
        estimat = _estimert_completion_oppstart(r)
        rader.append({
            "Tittel": r["tittel"], "Lenke": r["url"], "Felt": r["felt"], "Brønn": r["bronn"],
            "Operatør": r["operator"], "Rigg": navn, "Favorittrigg": bool(navn and navn in favoritter_nå),
            "Tidligst oppstart": r["tidligst_oppstart"], "Varighet (dager)": r["varighet_dager"],
            "Estimert completion-oppstart": estimat.isoformat() if estimat else None,
            "Saksnummer": r["saksnummer"], "Høringsfrist": r["frist"], "Frist utløpt": r["frist_utlopt"],
        })
    tabell = pd.DataFrame(rader, columns=[
        "Tittel", "Lenke", "Felt", "Brønn", "Operatør", "Rigg", "Favorittrigg",
        "Tidligst oppstart", "Varighet (dager)", "Estimert completion-oppstart",
        "Saksnummer", "Høringsfrist", "Frist utløpt",
    ])
    # Nullable heltall (Int64, stor I) i stedet for vanlig int: uten dette gjør pandas HELE
    # kolonnen om til flyttall (45 -> "45,0") så fort én eneste rad mangler varighet (None/NaN) —
    # rent kosmetisk, men unødvendig forvirrende å åpne i Excel.
    tabell["Varighet (dager)"] = tabell["Varighet (dager)"].astype("Int64")
    return tabell.to_csv(index=False, sep=";", decimal=",")


def _filtrer_operator_rigg(
    resultater: list[dict], alle_rigger: list[str], operator_valg: str, rigg_valg: str,
) -> list[dict]:
    """Filtrerer et allerede-hentet resultatsett på sidepanelets Operatør/Rigg-valg — samme filter
    som resten av appen (basis-dataframen i app.py). Ren funksjon, ingen Streamlit, så den kan
    testes for seg selv. Riggnavnet normaliseres mot Sodir-registeret (se _match_kjent_rigg()) FØR
    sammenligning, samme som alle andre steder riggnavn fra en høring brukes i denne filen — en
    høring med rigg «boreinnretningen Transocean Norge» skal matche et rigg-valg på «Transocean
    Norge», ikke bare et eksakt strengtreff."""
    ut = resultater
    if operator_valg != "Alle":
        ut = [r for r in ut if r.get("operator") == operator_valg]
    if rigg_valg != "Alle":
        ut = [r for r in ut if r.get("rigg") and _match_kjent_rigg(r["rigg"], alle_rigger)[0] == rigg_valg]
    return ut


def vis_fane(
    felt_liste: list[str], alle_rigger: list[str],
    ais: dict[str, dict] | None = None, steder: dict[str, tuple[float, float]] | None = None,
    operator_valg: str = "Alle", rigg_valg: str = "Alle",
) -> None:
    """Tegner hele «🔭 Høringer (tidlig varsel)»-fanen.

    `felt_liste`: alle kjente felt/funn fra Sodir-dataene (samme liste som sidepanelets
    «Felt / funn»-filter i app.py) — brukes både som standard søkeomfang og som valgene i
    «Ansvarsområde».
    `alle_rigger`: alle kjente riggnavn fra Sodir-dataene — brukes til å normalisere riggnavn
    regex finner i høringsteksten FØR de lagres som favoritt, se _match_kjent_rigg().
    `ais`/`steder`: samme live AIS-data og stedsoppslag som hovedkartet i app.py bruker — når
    begge er gitt, og en høring har en gjenkjent rigg MED live posisjon OG et gjenkjent felt,
    vises avstanden mellom dem (og et lite kart) — se avstands-/kart-blokken i løkken under.
    `operator_valg`/`rigg_valg`: Operatør/Rigg-valget fra sidepanelet i app.py (samme filter som
    gjelder resten av appen) — «Alle» betyr ingen ekstra filtrering her. Gjelder OVENPÅ
    «Ansvarsområde»/tidsgrense under, så du kan f.eks. kombinere «kun Nova» med «kun Equinor»."""
    st.caption(
        "Tidlig varsel: søker gjennom Miljødirektoratets høringer etter boring/brønn-relaterte "
        "saker — inkludert nye lete-/undersøkelsesbrønner som ennå ikke nødvendigvis er registrert "
        "i Sodir. Et grovt tittel-filter (boring/brønn-nøkkelord) brukes først; feltgjenkjenningen "
        "under er et beste forsøk med regex, ikke en garanti — se «sammendrag» for å sjekke selv."
    )
    horing_felt_valg = st.multiselect(
        "Ansvarsområde (felt/funn du vil se høringer om)", felt_liste, key="horing_felt_valg",
        help="Tomt = vis ALLE boring/brønn-relaterte høringer, uansett felt (bredt søk). Valgt "
             "ett eller flere felt: viser KUN høringer der det feltet faktisk ble gjenkjent i "
             "teksten — et presist søk, resten luftes ut.",
    )
    horing_tidsgrense_valg = st.selectbox(
        "Hvor langt tilbake i tid", list(HORING_TIDSGRENSE_ALTERNATIVER), index=0, key="horing_tidsgrense",
        help="Et TIDLIG VARSEL er mest nyttig for det som er ferskt. Filtreres etter at høringene "
             "er hentet, så en kortere grense gjør IKKE selve søket raskere — bare listen kortere.",
    )
    horing_dypt = st.checkbox(
        "Søk dypere i arkivet (flere sider, tar lengre tid)", value=False, key="horing_dypt",
        help="Av: henter bare de nyeste høringene innenfor tidsgrensen over. På: henter flere "
             "sider fra Miljødirektoratets eget API før den stopper, nyttig hvis du har satt en "
             "vid tidsgrense (eller «Alt») og vil være sikker på å ikke gå glipp av noe.",
    )
    if st.button("🔎 Søk i høringer nå", key="horing_sok_knapp"):
        st.session_state.horing_kjort = True

    if not st.session_state.get("horing_kjort"):
        st.write(
            "Trykk «Søk i høringer nå» for å sjekke Miljødirektoratet for boring/brønn-relaterte "
            "saker som ikke nødvendigvis er i Sodir ennå."
        )
        return

    kun_vis_kjente_felt = bool(horing_felt_valg)
    try:
        with st.spinner("Henter og tolker høringer fra Miljødirektoratet …"):
            kjente_felt = horing_felt_valg or felt_liste
            dager_tilbake = HORING_TIDSGRENSE_ALTERNATIVER[horing_tidsgrense_valg]
            resultater, feilet, alle_lenker, diag = _last_horinger(
                tuple(sorted(kjente_felt)), 200 if horing_dypt else 15, dager_tilbake, kun_vis_kjente_felt,
            )
    except Exception as e:
        st.error(f"Fikk ikke hentet høringer fra Miljødirektoratet: {e}")
        resultater, feilet, alle_lenker, diag = [], [], [], {}

    antall_for_sidepanelfilter = len(resultater)
    resultater = _filtrer_operator_rigg(resultater, alle_rigger, operator_valg, rigg_valg)

    # Lagres slik at tidslinje.py (en annen fane) kan vise akkurat disse resultatene uten å måtte
    # søke på nytt selv — se docstringen i tidslinje.vis_fane(). Lagres ETTER operatør/rigg-filteret
    # over, slik at tidslinjen respekterer akkurat samme filter som resten av appen.
    st.session_state.horing_siste_resultater = resultater

    # «NY siden sist»-merking: sammenligner mot URL-ene vi viste i et TIDLIGERE søk i denne
    # nettleserøkten (ikke lagret til disk — «sist du sjekket» mister mening etter en restart
    # uansett). Leses FØR vi oppdaterer den under, så "nye" telles riktig for akkurat dette søket.
    sett_tidligere = st.session_state.get("horing_sett_url", set())
    nye_urler = {r["url"] for r in resultater} - sett_tidligere

    st.caption(
        f"{len(alle_lenker)} høring(er) funnet totalt i kategorien «Olje og gass» (åpne + "
        f"avsluttede). {len(resultater)} vises under"
        + (f", herav {len(nye_urler)} nye siden forrige søk i denne økten" if sett_tidligere else "")
        + " — "
        + (f"matcher ansvarsområdet ditt ({', '.join(sorted(horing_felt_valg))})."
           if kun_vis_kjente_felt else "matchet boring/brønn-filteret (bredt søk, intet "
           "ansvarsområde valgt).")
        + (f" Filtrert ytterligere ned fra {antall_for_sidepanelfilter} av sidepanelets "
           f"Operatør/Rigg-valg." if (operator_valg != "Alle" or rigg_valg != "Alle") else "")
    )

    if diag:
        forklaring = STOPP_FORKLARING.get(diag.get("stoppet"), diag.get("stoppet"))
        total_tekst = (
            f" API-et oppgir {diag['total_ifolge_api']} høring(er) totalt i kategorien."
            if diag.get("total_ifolge_api") is not None else ""
        )
        st.caption(
            f"Henting fra Miljødirektoratets API: {diag.get('sider_hentet', 0)} side(r) hentet "
            f"— stoppet fordi: {forklaring}.{total_tekst}"
        )
        if diag.get("stoppet") == "feil" and diag.get("feilmelding"):
            st.caption(f"Feilmelding: {diag['feilmelding']}")

    if feilet:
        with st.expander(f"⚠️ {len(feilet)} høring(er) ble funnet, men kunne ikke åpnes/tolkes"):
            for f in feilet:
                st.write(f"- [{f['tittel']}]({f['url']}) — {f['feil']}")

    if not resultater:
        st.info("Ingen boring/brønn-relaterte høringer funnet med disse innstillingene ennå.")
    else:
        if "favoritter" not in st.session_state:
            st.session_state.favoritter = favoritter.last()
        for r in resultater:
            with st.container(border=True):
                ny_merke = "🆕 " if r["url"] in nye_urler else ""
                st.markdown(f"**{ny_merke}[{r['tittel']}]({r['url']})**")
                farge_merke = {"grønn": "🟢", "gul": "🟡", "grå": "⚪"}
                detaljer = []
                if r["felt"]:
                    farge, _ = _felt_sikkerhet(r)
                    detaljer.append(f"Felt: **{r['felt']}** {farge_merke[farge]}")
                if r["bronn"]:
                    detaljer.append(f"Brønn: **{r['bronn']}**")
                if r["operator"]:
                    detaljer.append(f"Operatør: **{r['operator']}**")
                if r["rigg"]:
                    detaljer.append(f"Rigg: **{r['rigg']}**")
                if r["tidligst_oppstart"]:
                    detaljer.append(f"Tidligst oppstart: **{r['tidligst_oppstart']}**")
                if r["varighet_dager"]:
                    detaljer.append(f"Oppgitt varighet: **{r['varighet_dager']} dager**")
                if r["frist"]:
                    frist_tekst = f"Høringsfrist: **{r['frist']}**"
                    if r["frist_utlopt"]:
                        frist_tekst += " (utløpt)"
                    detaljer.append(frist_tekst)
                if detaljer:
                    st.caption(" · ".join(detaljer))
                estimat = _estimert_completion_oppstart(r)
                if estimat:
                    st.caption(
                        f"⚙️ Estimert completion-oppstart: **ca {estimat.strftime('%d.%m.%Y')}** "
                        "— «tidligst forventet oppstart» i høringen + oppgitt varighet (antatt "
                        "ferdig boret-dato). Grovt anslag, IKKE en bekreftet dato fra Sodir eller "
                        "operatøren — boring kan forsinkes eller gå raskere enn oppgitt."
                    )
                if r["felt"]:
                    _, forklaring_sikkerhet = _felt_sikkerhet(r)
                    st.caption(f"Feltgjenkjenning: {forklaring_sikkerhet}")
                if r.get("felt_kontekst"):
                    st.caption(r["felt_kontekst"])
                elif not r["felt"]:
                    with st.expander("Sammendrag (felt ikke gjenkjent automatisk)"):
                        st.write(r["sammendrag"])

                if r["rigg"]:
                    navn, bekreftet = _match_kjent_rigg(r["rigg"], alle_rigger)

                    # Avstand/kart: krever BÅDE en live AIS-posisjon for riggen OG at feltet i
                    # høringen kan gjenkjennes som et kjent sted (se destinasjon.py). Viser hvor
                    # langt riggen har igjen til DIT HØRINGEN SIER den skal — ofte FØR mannskapet
                    # selv har oppdatert AIS-destinasjonen sin til det (det er jo poenget med et
                    # tidlig varsel).
                    ais_posisjon = ais.get(navn) if ais else None
                    fra = (
                        (ais_posisjon["lat"], ais_posisjon["lon"])
                        if ais_posisjon and ais_posisjon.get("lat") is not None
                        and ais_posisjon.get("lon") is not None else None
                    )
                    til = dest.finn_posisjon(r["felt"], steder) if r["felt"] and steder else None
                    if fra and til:
                        avstand = geoavstand.haversine_km(fra, til)
                        tid = geoavstand.estimert_reisetid_timer(avstand, ais_posisjon.get("fart"))
                        tekst = f"📍 «{navn}» er nå ca **{avstand:.0f} km** (luftlinje) fra {r['felt']}"
                        if tid is not None:
                            tekst += f" — ved nåværende fart ({ais_posisjon['fart']:.1f} knop), ca {tid:.0f} t"
                        st.caption(tekst + ".")
                        with st.expander("Vis på kart"):
                            st_folium(
                                pos.bygg_mini_kart(fra, til, navn, r["felt"]), height=320,
                                returned_objects=[], key=f"horing_minikart_{r['url']}",
                            )
                            st.caption(
                                "Stiplet linje = rett linje («som kråka flyr»), IKKE en faktisk "
                                "seilingsrute — til bruk som et grovt estimat."
                            )
                    elif fra and r["felt"] and not til:
                        st.caption(
                            f"📍 «{navn}» har en live AIS-posisjon, men «{r['felt']}» er ikke et "
                            "gjenkjent sted i stedsoppslaget ennå — ingen avstand kan vises."
                        )

                    # Favoritt-knapp: riggen regex fant i høringsteksten kan legges rett til
                    # favorittlisten herfra, i stedet for å måtte gå via sidepanelet og skrive
                    # navnet manuelt selv.
                    er_favoritt = navn in st.session_state.favoritter
                    if er_favoritt:
                        st.caption(f"⭐ «{navn}» er allerede en favorittrigg.")
                    else:
                        knapp_tekst = f"⭐ Legg «{navn}» til favoritter"
                        if st.button(knapp_tekst, key=f"horing_fav_{r['url']}"):
                            st.session_state.favoritter = st.session_state.favoritter | {navn}
                            favoritter.lagre(st.session_state.favoritter)
                            st.rerun()
                        if not bekreftet:
                            st.caption(
                                "⚠️ Dette riggnavnet ble ikke gjenkjent blant riggene i Sodir-dataene "
                                "ennå — lagres som skrevet i teksten, og dukker først opp andre steder "
                                "i appen hvis/når Sodir bruker akkurat denne stavemåten."
                            )

        st.download_button(
            "⬇️ Last ned høringene under som CSV (åpnes direkte i norsk Excel)",
            data=_eksporter_csv(resultater, alle_rigger).encode("utf-8-sig"),
            file_name=f"sokkelradar_horinger_{date.today().isoformat()}.csv", mime="text/csv",
            key="horing_csv_nedlasting",
            help="Eksakt det som vises over akkurat nå — endrer du ansvarsområde eller tidsgrense "
                 "og søker på nytt, endres også hva denne knappen laster ned.",
        )

    st.session_state.horing_sett_url = {r["url"] for r in resultater} | sett_tidligere