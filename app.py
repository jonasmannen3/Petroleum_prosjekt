"""Sokkelradar: oversikt over boring på norsk sokkel, basert på Sodir FactMaps.

Denne filen er bare UI-lim: den henter data via hent_data.py, bearbeider
dem via databehandling.py, og tegner kart/farger via posisjon.py.

Kjør:  streamlit run app.py
"""
from __future__ import annotations

from datetime import date, datetime

import pandas as pd
import streamlit as st
from streamlit_folium import st_folium

import barentswatch as bw
import databehandling as db
import destinasjon as dest
import epost
import fane_horinger
import favoritter
import hent_data
import legitimasjon
import oppsett_popup as op
import posisjon as pos
import riggbekreftelse
import sammendrag
import tidslinje

try:
    from streamlit_autorefresh import st_autorefresh
except ImportError:
    st_autorefresh = None  # pakken mangler: live-oppdatering vises som utilgjengelig i stedet for å krasje

st.set_page_config(page_title="Sokkelradar", layout="wide")

CACHE_SEKUNDER = 30 * 60
LIVE_MS = 15 * 60 * 1000

# Sodir sin egen, offisielle lisensoversikt — kolonnen «Licensing activity» der viser akkurat
# hvilken TFO/APA-runde (eller nummerert konsesjonsrunde) hver lisens ble tildelt i, og «Date
# granted» når. Lenkes til i stedet for å prøve å koble lisensnummeret appen allerede har (f.eks.
# «123») mot en egenkonstruert per-lisens-URL: Sodir sine faktaside-URL-er for enkeltlisenser bruker
# et internt NPDID-nummer vi IKKE har (det er ikke det samme som selve lisensnummeret) — en gjettet
# URL med feil ID ville tatt deg til en helt annen, tilfeldig lisens, noe som er verre enn ingen
# lenke i det hele tatt. Denne tabellsiden viser derimot ALLE lisenser med søk/sortering, verifisert
# å faktisk eksistere og inneholde riktig informasjon.
SODIR_LISENSOVERSIKT_URL = "https://factpages.sodir.no/en/licence/TableView/Overview"


@st.cache_data(ttl=CACHE_SEKUNDER, show_spinner=False)
def last_bronner(fra_ar: int):
    features = hent_data.hent_bronn_features(fra_ar)
    return db.tolk_bronner(features, hent_data.BRONN_FELT)


@st.cache_data(ttl=CACHE_SEKUNDER, show_spinner=False)
def last_innretninger():
    features = hent_data.hent_innretning_features()
    return db.tolk_innretninger(features, hent_data.INNR_FELT)


@st.cache_data(ttl=120, show_spinner=False)
def last_ais(klient_id: str, hemmelig: str, riggnavn: tuple[str, ...]) -> dict[str, dict]:
    """Hentes på nytt hvert 2. minutt (AIS beveger seg) — mye kortere enn CACHE_SEKUNDER for Sodir.
    Nøklene (klient-ID/hemmelighet) sendes aldri videre enn hit og til BarentsWatch selv."""
    token = bw.hent_token(klient_id, hemmelig)
    rader = bw.hent_siste_posisjoner(token)
    treff = bw.match_rigger(rader, list(riggnavn))
    return {rigg: bw.normaliser_posisjon(p) for rigg, p in treff.items()}


@st.dialog("🚀 Kom i gang med Sokkelradar")
def _vis_oppsett_popup():
    """To valgfrie ting samlet på ett sted, i stedet for at du må finne riktig expander selv (se
    oppsett_popup.py for HVORFOR appen ikke kan opprette selve kontoene for deg). Egne widget-keys
    (prefikset «popup_») fra sidepanelets/expanderens felt — samme felt vist to steder i samme
    skript-kjøring ville kollidert på nøkkel ellers."""
    st.caption(
        "To valgfrie ting du kan sette opp her — hopp over det du ikke vil ha nå. Du finner begge "
        "igjen i sidepanelet («BarentsWatch») og i «📧 Ukentlig sammendrag»-expanderen nederst "
        "i appen når du vil justere dem senere."
    )

    st.markdown("**📡 Live riggposisjon (BarentsWatch)**")
    st.caption(
        "Krever en gratis «AIS-client»-konto hos BarentsWatch (med BankID) — det kan jeg ikke "
        "opprette for deg, men du limer bare inn nøkkelen under når du har den: "
        "[barentswatch.no/minside](https://www.barentswatch.no/minside/)."
    )
    p_bw_id = st.text_input("BarentsWatch klient-ID", key="popup_bw_id", type="password")
    p_bw_hemmelig = st.text_input("BarentsWatch hemmelighet", key="popup_bw_hemmelig", type="password")
    if st.button("💾 Lagre BarentsWatch-nøkkel", key="popup_bw_lagre"):
        if p_bw_id and p_bw_hemmelig:
            legitimasjon.lagre(p_bw_id, p_bw_hemmelig)
            st.session_state.bw_id = p_bw_id
            st.session_state.bw_hemmelig = p_bw_hemmelig
            st.success("Lagret. Live posisjon aktiveres neste gang siden lastes på nytt.")
        else:
            st.warning("Fyll inn begge feltene først.")

    st.divider()
    st.markdown("**📧 Ukentlig/daglig sammendrag på e-post**")
    p_frekvens = st.radio("Hvor ofte?", ["Ukentlig", "Daglig"], key="popup_frekvens", horizontal=True,
                          help="Styrer bare hvor langt frem sammendraget ser, og ordet i emnet — HVOR "
                               "OFTE det faktisk sendes avgjøres av hvor ofte du (eller en systemplanlegger) "
                               "trykker «Send»/kjører send_ukentlig_sammendrag_cli.py.")
    p_hva = st.multiselect(
        "Hva vil du følge?", ["Brønnoppstart", "Høringer (tidlig varsel)"],
        default=["Brønnoppstart", "Høringer (tidlig varsel)"], key="popup_hva",
    )
    p_bare_fav = st.checkbox("Bare favorittriggene mine", key="popup_bare_fav",
                             help="Begrenser sammendraget til rader der en gjenkjent rigg er blant "
                                  "favorittriggene dine i sidepanelet.")
    p_epost = st.text_input("Din e-postadresse", key="popup_epost", placeholder="din-epost@eksempel.no")
    p_passord = st.text_input(
        "App-passord for samme konto", key="popup_passord", type="password",
        help="Brukes til å SENDE sammendraget fra og TIL samme adresse (send-til-deg-selv). Bruk et "
             "eget app-passord/engangspassord der leverandøren tilbyr det, ikke hovedpassordet ditt.",
    )
    gjettet = op.gjett_smtp_server(p_epost) if p_epost else None
    if p_epost and not gjettet:
        st.caption(
            "Kjenner ikke denne e-postleverandøren automatisk — fyll inn SMTP-server/port manuelt i "
            "«📧 Ukentlig sammendrag»-expanderen nederst i appen i stedet (de andre valgene over "
            "lagres der også hvis du trykker «Aktiver» her først)."
        )
    elif gjettet:
        st.caption(f"Gjettet SMTP-server: {gjettet[0]}:{gjettet[1]} (kan endres i expanderen senere).")

    if st.button("✅ Aktiver sammendrag", key="popup_epost_lagre"):
        if not p_epost or not p_passord:
            st.warning("Fyll inn e-post og app-passord først.")
        else:
            innstillinger = {
                "smtp_server": (gjettet or ("", 587))[0], "smtp_port": (gjettet or ("", 587))[1],
                "bruker": p_epost, "passord": p_passord, "mottaker": p_epost,
                "frekvens": "daglig" if p_frekvens == "Daglig" else "ukentlig",
                "inkluder_bronner": "Brønnoppstart" in p_hva,
                "inkluder_horinger": "Høringer (tidlig varsel)" in p_hva,
                "bare_favoritter": p_bare_fav,
            }
            epost.lagre(innstillinger)
            st.session_state.smtp = innstillinger
            if gjettet:
                st.success(
                    "Aktivert! Prøv «📤 Send dette sammendraget nå» i «📧 Ukentlig sammendrag»"
                    "-expanderen nederst i appen for å teste at det virker."
                )
            else:
                st.warning("Lagret valgene — men SMTP-server er ukjent, så fyll den inn manuelt i "
                          "expanderen nederst før du kan sende noe.")

    st.divider()
    c1, c2 = st.columns(2)
    if c1.button("Ikke nå (spør igjen senere)", key="popup_ikke_na"):
        op.lagre_tilstand(False, date.today().isoformat())
        st.rerun()
    if c2.button("Spør meg ikke om dette igjen", key="popup_aldri"):
        op.lagre_tilstand(True, date.today().isoformat())
        st.rerun()


# last_horinger()/hele «Høringer»-fanens UI er flyttet til fane_horinger.py (se vis_fane() der),
# siden denne fila ellers vokste seg for stor — app.py kaller bare fane_horinger.vis_fane() under.


# ---------- Live-oppdatering ----------
# Når bryteren er på, kjører st_autorefresh en ny visning hvert 15. minutt, og da må
# @st.cache_data-bufferet tømmes først, ellers ville siden kjørt på nytt uten å hente nytt fra Sodir.
if "live" not in st.session_state:
    st.session_state.live = False
if "sist_hentet" not in st.session_state:
    st.session_state.sist_hentet = None
if "bw_id" not in st.session_state:
    st.session_state.bw_id = ""
if "bw_hemmelig" not in st.session_state:
    st.session_state.bw_hemmelig = ""
if "bw_lastet_fra_disk" not in st.session_state:
    # Kjøres bare én gang per økt: fyller inn en tidligere lagret nøkkel (se legitimasjon.py),
    # men overskriver aldri noe du allerede har skrevet inn i denne økten.
    _lagret = legitimasjon.last()
    if _lagret["klient_id"] and not st.session_state.bw_id:
        st.session_state.bw_id = _lagret["klient_id"]
    if _lagret["hemmelig"] and not st.session_state.bw_hemmelig:
        st.session_state.bw_hemmelig = _lagret["hemmelig"]
    st.session_state.bw_lastet_fra_disk = True
bw_id = st.session_state.bw_id
bw_hemmelig = st.session_state.bw_hemmelig

# Samme opplastingsmønster som BarentsWatch-nøkkelen over, for SMTP-innstillingene til det
# ukentlige sammendraget (se epost.py/sammendrag.py og expanderen «📧 Ukentlig sammendrag» nederst).
if "smtp" not in st.session_state:
    st.session_state.smtp = epost.last()

with st.sidebar:
    st.title("Sokkelradar")
    st.caption("Boring på norsk sokkel, hentet fra Sodir.")
    live_stott = st_autorefresh is not None
    st.session_state.live = st.toggle(
        "Live-oppdatering (hvert 15. min)", value=st.session_state.live if live_stott else False,
        disabled=not live_stott,
        help=None if live_stott else "Pakken streamlit-autorefresh mangler. Legg den til i requirements.txt.",
    )

if st.session_state.live and st_autorefresh:
    tikk = st_autorefresh(interval=LIVE_MS, key="live_autorefresh")
    if tikk and tikk != st.session_state.get("_siste_tikk"):
        st.session_state._siste_tikk = tikk
        st.cache_data.clear()  # tving ny henting fra Sodir ved hvert 15.-minutters tikk

topp_venstre, topp_hoyre = st.columns([1, 5])
with topp_venstre:
    if st.session_state.live:
        st.markdown(
            "<div style='display:flex;align-items:center;gap:7px;margin-top:6px'>"
            "<span style='width:10px;height:10px;border-radius:50%;background:#e2531f;"
            "box-shadow:0 0 0 0 rgba(226,83,31,.6);animation:puls 1.6s infinite'></span>"
            "<span style='font:700 13px sans-serif;letter-spacing:.06em;color:#e2531f'>LIVE</span></div>"
            "<style>@keyframes puls{0%{box-shadow:0 0 0 0 rgba(226,83,31,.6)}"
            "70%{box-shadow:0 0 0 8px rgba(226,83,31,0)}100%{box-shadow:0 0 0 0 rgba(226,83,31,0)}}</style>",
            unsafe_allow_html=True,
        )

# ---------- Sidepanel, del 1 ----------
idag = date.today()
with st.sidebar:
    fra_ar, til_ar = st.slider(
        "Brønner startet i perioden", min_value=idag.year - 15, max_value=idag.year,
        value=(idag.year - 2, idag.year), help=(
            "Gjelder brønner med en faktisk startdato (ferdig boret eller borer nå). Tillatte, planlagte "
            "og «annet»-brønner (uten startdato) vises uansett, siden det er de du vil se lengst frem i tid."))
    valgte = st.multiselect("Vis på kartet", list(db.KATEGORI_NAVN.values()),
                            default=[v for k, v in db.KATEGORI_NAVN.items() if k not in ("annet", "avlyst")],
                            help="«Annet / uavklart» og «Avlyst» er av som standard — se forklaringen i "
                                 "fanene med samme navn før du skrur dem på.")
    modus = st.selectbox("Fargelegg brønner etter", list(pos.FARGEMODUS), help=(
        "Status: oransje = borer nå, blå = planlagt, lilla stiplet = annet/uavklart, lys grå stiplet = "
        "avlyst, grå = ferdig. De andre valgene farger etter operatør, formål eller rigg-type, og formen "
        "viser da status."))
    vis_innr = st.checkbox("Innretninger på feltet", value=True)
    innr_fra_ar = 1965
    if vis_innr:
        innr_fra_ar = st.slider(
            "Innretninger: oppstart etter år", min_value=1965, max_value=idag.year, value=1965, help=(
                "Eget filter for innretninger (plattformer, FPSO-er o.l.) — helt uavhengig av brønn-"
                "tidslinjen over. En innretning sluttet ikke å være relevant bare fordi den ble satt i "
                "drift for lenge siden, så standard er å vise alle. Innretninger uten registrert "
                "oppstartsdato i Sodir vises uansett hvor du setter denne."))
    bakgrunn = st.selectbox("Bakgrunnskart", list(pos.BAKGRUNN))
    kompakt = st.toggle(
        "📱 Kompakt modus (mobil)", value=False,
        help="Lavere kart og færre kolonner i tabellene — de samme dataene, bare kortet ned til "
             "det viktigste for en rask sjekk på mobilen. CSV-nedlastingen inneholder uansett "
             "ALLE kolonner, uavhengig av dette valget.",
    )

# ---------- Hent data ----------
try:
    with st.spinner("Henter data fra Sodir …"):
        bronner, stat = last_bronner(fra_ar)
    st.session_state.sist_hentet = datetime.now()
except Exception as e:  # nettverk, feil fra Sodir, uventet format
    st.error(f"Fikk ikke kontakt med Sodir: {e}")
    st.stop()

try:
    innr = last_innretninger()
except Exception as e:
    innr = pd.DataFrame(columns=hent_data.INNR_KOLONNER)
    st.warning(f"Innretninger kunne ikke hentes ({e}). Brønnene vises likevel.")

# Innretninger som faktisk tegnes på kartet, filtrert på "Innretninger: oppstart etter år" over.
# Den UFILTRERTE innr brukes fortsatt til stedsoppslaget for AIS-destinasjon lenger ned — en eldre
# innretning er like gyldig å gjenkjenne en AIS-destinasjon mot, selv om den er skjult på kartet akkurat nå.
innr_vis = innr
if vis_innr and not innr.empty:
    innr_vis = innr[innr["oppstart"].isna() | (innr["oppstart"].dt.year >= innr_fra_ar)]

# ---------- Sidepanel, del 2: filtre som avhenger av dataene ----------
if "favoritter" not in st.session_state:
    st.session_state.favoritter = favoritter.last()

with st.sidebar:
    operatorer = ["Alle"] + sorted(bronner["operator"].dropna().unique())
    alle_rigger = sorted(bronner["rigg"].dropna().unique())
    rigger = ["Alle"] + alle_rigger
    operator_valg = st.selectbox("Operatør", operatorer)
    rigg_valg = st.selectbox("Rigg", rigger)
    omraade_valg = st.multiselect("Område", sorted(bronner["omraade"].dropna().unique()),
                                  help="Tomt = vis alle områder.")
    felt_liste = sorted(set(bronner["felt"].dropna()) | set(bronner["funn"].dropna()))
    felt_valg = st.multiselect("Felt / funn", felt_liste, help="Tomt = vis alle felt/funn.")
    status_liste = sorted(bronner["status"].dropna().unique())
    status_valg = st.multiselect("Detaljert status (rå fra Sodir)", status_liste,
                                 help="Finere filter enn de 4 hovedkategoriene over — f.eks. for å skille "
                                      "SUSPENDED fra P&A innenfor «Ferdig boret». Tomt = vis alle.")
    sok = st.text_input("Søk i brønn, felt, rigg eller lisens", placeholder="For eksempel 34/10")

    with st.expander(f"⭐ Favorittrigger ({len(st.session_state.favoritter)})"):
        # Rigger du har merket tidligere, men som ikke er i utvalget nå (f.eks. ferdig boret), listes også,
        # så du ikke mister favoritten din bare fordi den ikke dukker opp i dagens filtrering.
        valgbare = sorted(set(alle_rigger) | st.session_state.favoritter)
        valgt = st.multiselect("Rigger å følge", valgbare, default=sorted(st.session_state.favoritter))
        if set(valgt) != st.session_state.favoritter:
            st.session_state.favoritter = set(valgt)
            favoritter.lagre(st.session_state.favoritter)
        if not valgbare:
            st.caption("Ingen rigger i dataene ennå.")

    if st.button("Oppdater data nå"):
        st.cache_data.clear()
        st.rerun()
    if st.session_state.sist_hentet:
        st.caption("Sist hentet kl. " + st.session_state.sist_hentet.strftime("%H:%M:%S"))

# ---------- Live AIS-posisjon (BarentsWatch) ----------
# Krever at du har lagt inn egen klient-ID/hemmelighet nederst i siden (se expanderen). Rigger
# uten AIS-treff vises bare uten den ekstra markøren — ikke en feil, se bw.match_rigger.
ais: dict[str, dict] = {}
ais_feil: str | None = None
if bw_id and bw_hemmelig and alle_rigger:
    try:
        ais = last_ais(bw_id, bw_hemmelig, tuple(alle_rigger))
    except bw.BarentsWatchFeil as e:
        ais_feil = str(e)

# Riggens kategori (borer > planlagt > annet > ferdig, det mest aktuelle for drift) brukes til å
# farge AIS-markøren likt som brønnen den er koblet til, i stedet for alltid samme rød farge.
if ais:
    _prioritet = {"borer": 0, "planlagt": 1, "annet": 2, "avlyst": 3, "ferdig": 4}
    for rigg, p in ais.items():
        g = bronner[bronner["rigg"] == rigg]
        if g.empty:
            continue
        p["kategori"] = min(g["kategori"], key=lambda k: _prioritet.get(k, 9))

# Stedsoppslag for å tolke AIS sin fritekst-destinasjon (se destinasjon.py) — bygges uansett om
# AIS er aktivt, siden det er en billig operasjon på data vi allerede har i minnet.
steder = dest.bygg_stedsoppslag(innr, bronner)

# ---------- Filtrering ----------
basis = bronner
if operator_valg != "Alle":
    basis = basis[basis["operator"] == operator_valg]
if rigg_valg != "Alle":
    basis = basis[basis["rigg"] == rigg_valg]
if omraade_valg:
    basis = basis[basis["omraade"].isin(omraade_valg)]
if felt_valg:
    basis = basis[basis["felt"].isin(felt_valg) | basis["funn"].isin(felt_valg)]
if status_valg:
    basis = basis[basis["status"].isin(status_valg)]
# Øvre grense på tidslinjen: gjelder bare brønner med en faktisk startdato (planlagt/annet har ingen).
basis = basis[basis["start"].isna() | (basis["start"].dt.year <= til_ar)]
if sok.strip() and not basis.empty:
    kolonner = ["navn", "felt", "funn", "rigg", "operator", "omraade", "lisens"]
    heystakk = basis[kolonner].fillna("").astype(str).agg(" ".join, axis=1).str.lower()
    basis = basis[heystakk.str.contains(sok.strip().lower(), regex=False)]
kategorier = [k for k, navn in db.KATEGORI_NAVN.items() if navn in valgte]
synlig = basis[basis["kategori"].isin(kategorier)]

fargekol = pos.FARGEMODUS[modus]
fargekart = pos.lag_fargekart(bronner[fargekol]) if fargekol else {}

# ---------- Hovedvisning ----------
antall = basis["kategori"].value_counts()
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric(db.KATEGORI_NAVN["borer"], int(antall.get("borer", 0)))
c2.metric(db.KATEGORI_NAVN["planlagt"], int(antall.get("planlagt", 0)))
c3.metric(db.KATEGORI_NAVN["annet"], int(antall.get("annet", 0)))
c4.metric(db.KATEGORI_NAVN["avlyst"], int(antall.get("avlyst", 0)))
c5.metric(db.KATEGORI_NAVN["ferdig"], int(antall.get("ferdig", 0)))

kart_kol, liste_kol = st.columns([3, 2])
with kart_kol:
    st.markdown(pos.legende_html(synlig, modus, fargekol, fargekart, vis_innr, len(ais)),
                unsafe_allow_html=True)
    st_folium(pos.bygg_kart(synlig, innr_vis, vis_innr, bakgrunn, fargekol, fargekart, ais, steder),
              height=380 if kompakt else 680, returned_objects=[], key="kart")
    if ais_feil:
        st.warning(f"Fikk ikke AIS-data fra BarentsWatch: {ais_feil}")

with liste_kol:
    fane0, fane1, fane2, fane2b, fane2c, fane2d, fane5, fane6, fane3, fane4 = st.tabs(
        ["⭐ Favoritter", "Rigger som borer nå", "Tillatt eller planlagt", "Kommende rigger",
         "Annet / uavklart", "Avlyst", "🔭 Høringer (tidlig varsel)", "🗓️ Tidslinje",
         "Per operatør", "Alle brønner"])
    with fane0:
        if not st.session_state.favoritter:
            st.write("Ingen favorittrigger ennå. Legg til under «⭐ Favorittrigger» i sidepanelet.")
        else:
            egne = basis[basis["rigg"].isin(st.session_state.favoritter)]
            if egne.empty:
                st.write("Ingen av favorittriggene dine har brønner som passer filtrene nå.")
            else:
                for rigg in sorted(st.session_state.favoritter):
                    g = egne[egne["rigg"] == rigg]
                    if g.empty:
                        continue
                    for kat in ("borer", "planlagt", "annet", "avlyst", "ferdig"):
                        gg = g[g["kategori"] == kat]
                        if not gg.empty:
                            st.markdown(f"**{rigg}** — {db.KATEGORI_NAVN[kat]}")
                            favoritt_kolonner = (
                                ["Brønn", "Planlagt start"] if kompakt
                                else ["Brønn", "Operatør", "Planlagt start", "Startet"]
                            )
                            st.dataframe(db.forenkle_kolonner(db.alle_bronner(gg), favoritt_kolonner),
                                        hide_index=True)
    with fane1:
        sorter = st.selectbox("Sorter etter", db.RIGG_SORTERING, key="sorter_rigg")
        tabell = db.rigger_som_borer(synlig, sorter)
        if tabell.empty:
            st.write("Ingen brønner har status «borer» med disse filtrene.")
        else:
            vis = db.forenkle_kolonner(tabell, ["Rigg", "Brønn", "Dager"]) if kompakt else tabell
            st.dataframe(pos.tabell_farget(vis, modus, fargekart), hide_index=True)
    with fane2:
        sorter = st.selectbox("Sorter etter", db.PLAN_SORTERING, key="sorter_plan")
        tabell = db.planlagte(synlig, sorter)
        if tabell.empty:
            st.write("Ingen tillatte eller planlagte brønner med disse filtrene.")
        else:
            vis = db.forenkle_kolonner(
                tabell, ["Brønn", "Planlagt start", "Dager til start", "Merknad"]
            ) if kompakt else tabell
            st.dataframe(pos.tabell_farget(vis, modus, fargekart), hide_index=True)
            st.caption(
                "«Dager til start» er negativt når planlagt dato er passert — betyr ikke feil data, "
                "bare at Sodir ennå ikke har oppdatert status for den brønnen. «Merknad» varsler "
                f"⚠️ når det er over {-db.GRENSE_FORSINKET_DAGER} dager siden."
            )
    with fane2b:
        tabell = db.rigger_planlagt(synlig)
        if tabell.empty:
            st.write("Ingen rigger med tillatte eller planlagte brønner med disse filtrene.")
        else:
            horinger_for_bekreftelse = st.session_state.get("horing_siste_resultater")
            if horinger_for_bekreftelse:
                bekreftelser = riggbekreftelse.siste_bekreftelse_per_rigg(horinger_for_bekreftelse, alle_rigger)
                tabell = riggbekreftelse.beriket_med_bekreftelse(tabell, bekreftelser)
                ekstra = riggbekreftelse.nye_rader_fra_horinger(tabell, bekreftelser)
                if not ekstra.empty:
                    tabell = pd.concat([tabell, ekstra], ignore_index=True)
            skjul_forsinket = st.checkbox(
                "Skjul trolig forsinkede/stillestående rigger (⚠️)", value=False, key="skjul_forsinket_rigger",
                help="Sodir sier ikke selv om en ⚠️-merket rigg er kansellert, utsatt eller bare "
                     "glemt i registeret — appen gjetter bevisst ikke på det. Denne bryteren "
                     "SKJULER dem bare fra visningen her (ingenting slettes, og CSV-eksport under "
                     "«Alle brønner»-fanen har dem fortsatt med).",
            )
            vis_tabell = tabell[~tabell["Merknad"].str.startswith("⚠️")] if skjul_forsinket else tabell
            if skjul_forsinket and vis_tabell.empty and not tabell.empty:
                st.write("Alle riggene med disse filtrene er ⚠️-merket — ingen igjen å vise med bryteren på.")
            else:
                st.dataframe(vis_tabell, hide_index=True)
            st.caption(
                "Rigger som ikke har «entered» en brønn ennå, altså ikke startet boring. «Rigg ikke "
                "tildelt» betyr at Sodir ikke har registrert hvilken rigg som skal bore brønnen."
            )
            st.caption(
                "«Dager til» er negativt når datoen er passert — det betyr ikke feil data, bare at "
                "Sodir ennå ikke har oppdatert status (start/ferdig/avlyst) for den brønnen. "
                f"⚠️ vises når det er over {-db.GRENSE_FORSINKET_DAGER} dager siden — et tegn på at "
                "prosjektet trolig er forsinket eller lagt på is, ikke en bekreftet kansellering. ✅ "
                "betyr at riggen nylig ble nevnt i en fersk høring hos Miljødirektoratet — enten som "
                "bekreftelse på en rad over, eller (uten «Dager til») en rigg Sodir ikke har satt opp "
                "som «planlagt» i det hele tatt ennå. Krever at du har kjørt et søk i «Høringer»-fanen "
                "først — det gjøres ikke automatisk her, siden selve søket tar noen sekunder."
            )
    with fane2c:
        tabell = db.annet_tabell(basis, "annet")
        if tabell.empty:
            st.write("Ingen brønner i denne kategorien med disse filtrene.")
        else:
            st.caption(
                "Brønner uten startdato, planlagt start eller boretillatelse i Sodir, og IKKE markert «vil "
                "aldri bli boret» (de ligger i egen fane, «Avlyst»). Dette er brønner i tidlig fase — lisens/"
                "brønn registrert i Sodir, men ingen dato satt ennå — som kan være relevante å følge med på "
                "for lower completion selv om Sodir ikke har formalisert en dato. Rå status i egen kolonne, "
                "så du kan vurdere selv hva som faktisk ligger bak den."
            )
            vis = db.forenkle_kolonner(
                tabell, ["Brønn", "Rigg", "Operatør", "Status (rått fra Sodir)"]
            ) if kompakt else tabell
            st.dataframe(vis, hide_index=True)
            st.caption(
                f"Vil du se hvilken TFO/APA-runde «Lisens»-nummeret over ble tildelt i (og når)? "
                f"Søk opp nummeret i [Sodir sin offisielle lisensoversikt]({SODIR_LISENSOVERSIKT_URL}) "
                "— kolonnen «Licensing activity» der viser runden."
            )
    with fane2d:
        tabell = db.annet_tabell(basis, "avlyst")
        if tabell.empty:
            st.write("Ingen avlyste brønner med disse filtrene.")
        else:
            st.caption(
                "Brønner der Sodir sin status eksplisitt sier brønnen aldri skal bores. Skilt ut fra «Annet "
                "/ uavklart» nettopp for at disse skal kunne skrus av for seg selv — se avkrysningen «Vis på "
                "kartet» i sidepanelet."
            )
            vis = db.forenkle_kolonner(
                tabell, ["Brønn", "Rigg", "Operatør", "Status (rått fra Sodir)"]
            ) if kompakt else tabell
            st.dataframe(vis, hide_index=True)
            st.caption(
                f"Vil du se hvilken TFO/APA-runde «Lisens»-nummeret over ble tildelt i (og når)? "
                f"Søk opp nummeret i [Sodir sin offisielle lisensoversikt]({SODIR_LISENSOVERSIKT_URL}) "
                "— kolonnen «Licensing activity» der viser runden."
            )
    with fane5:
        fane_horinger.vis_fane(felt_liste, alle_rigger, ais, steder, operator_valg, rigg_valg)
    with fane6:
        tidslinje.vis_fane(basis)
    with fane3:
        tabell = db.per_operator(basis)
        st.dataframe(pos.tabell_farget(tabell, modus, fargekart), hide_index=True)
        st.caption("Alle brønner som passer filtrene, uavhengig av hvilke kategorier som vises på kartet.")
    with fane4:
        tabell = db.alle_bronner(synlig)
        vis = db.forenkle_kolonner(
            tabell, ["Brønn", "Kategori", "Operatør", "Rigg", "Planlagt start", "Startet"]
        ) if kompakt else tabell
        st.dataframe(pos.tabell_farget(vis, modus, fargekart), hide_index=True)
        st.caption("Klikk på en kolonneoverskrift for å sortere."
                   + (" (Kompakt modus: kun et utvalg kolonner vist — CSV-en under har alle.)" if kompakt else ""))
        st.caption(
            f"Vil du se hvilken TFO/APA-runde «Lisens»-nummeret på en brønn ble tildelt i (og når)? "
            f"Søk opp nummeret i [Sodir sin offisielle lisensoversikt]({SODIR_LISENSOVERSIKT_URL}) "
            "— kolonnen «Licensing activity» der viser runden."
        )
        st.download_button(
            "Last ned som CSV (åpnes direkte i norsk Excel)",
            data=tabell.to_csv(index=False, sep=";", decimal=",").encode("utf-8-sig"),
            file_name=f"sokkelradar_bronner_{idag.isoformat()}.csv", mime="text/csv",
        )

with st.expander("Om dataene"):
    st.write(
        f"{stat['hentet']} brønner hentet fra Sodir, hvorav {stat['geoteknisk_boring']} var rene "
        "geotekniske grunnundersøkelser («formål: SOIL DRILLING» — ikke leting/produksjon av olje/"
        f"gass) og er utelatt helt. Av de {len(bronner)} gjenværende havnet {stat['annet']} i «Annet "
        f"/ uavklart», {stat['avlyst']} i «Avlyst», og {stat['uten_posisjon']} manglet posisjon og "
        "ble utelatt helt."
    )
    st.caption("Sodir har ikke et eget «borer nå»-felt, så kategoriene er tolket fra status og datoer. "
               "Tabellen under viser hvilke rå statusverdier som havnet i hvilken kategori (allerede "
               "uten de geotekniske grunnundersøkelsene).")
    st.dataframe(stat["statusverdier"], hide_index=True)
    st.caption("Posisjoner er omtrentlige (0–300 m) og skal ikke brukes til navigasjon. Data: Sodir, lisens NLOD.")

with st.expander("Om BarentsWatch, Miljødirektoratet og Kystdatahuset"):
    st.markdown(
        "- **BarentsWatch** er koblet til under — live AIS-posisjon vises som en rød, pulserende markør på "
        "kartet for rigger som har et navnetreff. Matching skjer på navn (ikke MMSI, som Sodir ikke har), så "
        "sjekk «delvis treff» i tabellen under før du stoler på det.\n"
        "- **Miljødirektoratets høringer** («🔭 Høringer (tidlig varsel)»-fanen) hentes via nettstedets eget "
        "interne API, kjørt inne i en hodeløs nettleser (for å slippe forbi Cloudflare sin bot-sjekk, som "
        "blokkerer vanlige forespørsler). Dette er et tidlig varsel om kommende lete-/undersøkelsesbrønner "
        "FØR de nødvendigvis dukker opp i Sodir sitt register — feltgjenkjenningen er regex-basert og ikke "
        "garantert riktig, så bruk «sammendrag» til å sjekke selv.\n"
        "- **Kystdatahuset** er jeg usikker på om er åpent uten avtale. Undersøkes nærmere."
    )
    bw1, bw2 = st.columns(2)
    bw1.text_input("BarentsWatch klient-ID", key="bw_id", type="password")
    bw2.text_input("BarentsWatch hemmelighet", key="bw_hemmelig", type="password")
    st.caption(
        "Nøkkelen brukes kun til å sende forespørsler til BarentsWatch. Opprettes gratis som «AIS-client» "
        "(ikke «API-client») på barentswatch.no/minside."
    )
    bw3, bw4 = st.columns(2)
    if bw3.button("💾 Lagre nøkkel på denne maskinen"):
        if legitimasjon.lagre(st.session_state.bw_id, st.session_state.bw_hemmelig):
            st.success("Lagret lokalt i barentswatch_nokkel.json — i klartekst, i prosjektmappen din. "
                       "Slipper å skrives inn på nytt neste gang du starter appen.")
        else:
            st.warning("Kunne ikke lagre til disk her (f.eks. skrivebeskyttet mappe). Nøkkelen fungerer "
                      "likevel i denne økten, men må skrives inn på nytt neste gang.")
    if bw4.button("🗑️ Glem lagret nøkkel"):
        legitimasjon.slett()
        st.session_state.bw_id = ""
        st.session_state.bw_hemmelig = ""
        st.rerun()
    if not (bw_id and bw_hemmelig):
        st.caption("Fyll inn begge feltene for å slå på live riggposisjon.")
    elif ais_feil:
        st.warning(f"Fikk ikke AIS-data: {ais_feil}")
    elif ais:
        st.success(f"{len(ais)} av {len(alle_rigger)} rigger har en AIS-posisjon akkurat nå.")

        def _destinasjon_status(p: dict) -> str:
            tekst = p.get("destinasjon")
            if not tekst:
                return "(ingen destinasjon oppgitt)"
            funnet = dest.finn_posisjon(tekst, steder)
            return f"Ja — tegnes til {funnet[0]:.3f}, {funnet[1]:.3f}" if funnet else "Nei — teksten gjenkjennes ikke"

        st.dataframe(pd.DataFrame([
            {"Rigg": rigg, "Fartøynavn (AIS)": p.get("navn"), "Treff": p.get("match"),
             "Fart (knop)": p.get("fart"), "Sist meldt": p.get("tid"),
             "Oppgitt destinasjon": p.get("destinasjon"), "Oppgitt ETA": p.get("eta"),
             "Linje på kartet?": _destinasjon_status(p)}
            for rigg, p in sorted(ais.items())
        ]), hide_index=True)
        st.caption("Destinasjon/ETA er fritekst fra mannskapet selv (ikke alltid tilgjengelig) — ofte "
                  "forkortet eller utdatert. «Linje på kartet?» viser om teksten ble gjenkjent som et "
                  "kjent sted (forsyningsbase, innretning eller feltnavn) — hvis «Nei», er teksten enten "
                  "ikke i datagrunnlaget vårt eller tvetydig, og appen gjetter da bevisst ikke.")
        st.caption(f"({len(steder)} kjente stedsnavn i oppslaget: forsyningsbaser, Sodir-innretninger og feltnavn.)")
    else:
        st.info("Nøkkel er satt, men ingen av riggene i dataene har et AIS-navnetreff akkurat nå.")

with st.expander("📧 Ukentlig sammendrag (e-post)"):
    st.caption(
        "Lager en ferdig e-posttekst med nye høringer siden sist, og brønnoppstart/høringer de "
        "neste 14 dagene — basert på akkurat de dataene og filtrene appen viser akkurat nå. "
        "VIKTIG: appen her kan bare generere/sende et sammendrag NÅR DEN ER ÅPEN, siden en "
        "Streamlit-side ikke kan våkne av seg selv en gang i uken. For faktisk automatisk, ukentlig "
        "sending uten at noen har siden åpen, se send_ukentlig_sammendrag_cli.py — et frittstående "
        "skript ment kjørt av en systemplanlegger (f.eks. Windows Oppgaveplanlegger eller cron), "
        "som bruker de samme lagrede SMTP-innstillingene som settes inn her."
    )
    e1, e2 = st.columns(2)
    e1.text_input("SMTP-server", key="smtp_server_felt", value=st.session_state.smtp["smtp_server"],
                  placeholder="f.eks. smtp.gmail.com")
    e2.number_input("SMTP-port", key="smtp_port_felt", value=st.session_state.smtp["smtp_port"],
                    step=1, help="587 (STARTTLS) er vanligst. 465 (SSL) hvis leverandøren krever det.")
    e3, e4 = st.columns(2)
    e3.text_input("Avsender (brukernavn)", key="smtp_bruker_felt", value=st.session_state.smtp["bruker"])
    e4.text_input("Passord / app-passord", key="smtp_passord_felt", type="password",
                  value=st.session_state.smtp["passord"],
                  help="Bruk et eget app-passord/engangspassord der leverandøren tilbyr det "
                       "(f.eks. Gmail), i stedet for hovedpassordet ditt.")
    st.text_input("Mottaker", key="smtp_mottaker_felt", value=st.session_state.smtp["mottaker"],
                 placeholder="din-egen-epost@eksempel.no")
    st.selectbox("Frekvens", ["ukentlig", "daglig"], key="smtp_frekvens_felt",
                index=["ukentlig", "daglig"].index(st.session_state.smtp.get("frekvens", "ukentlig")),
                help="Styrer bare hvor mange dager fremover sammendraget ser på (14 for ukentlig, "
                     "3 for daglig) og ordet i emnefeltet — HVOR OFTE det faktisk sendes avgjøres "
                     "av hvor ofte du (eller en systemplanlegger) trykker «Send»/kjører CLI-skriptet.")
    e7, e8 = st.columns(2)
    inkluder_bronner_felt = e7.checkbox("Inkluder brønnoppstart", key="smtp_inkluder_bronner_felt",
                                        value=st.session_state.smtp.get("inkluder_bronner", True))
    inkluder_horinger_felt = e8.checkbox("Inkluder høringer", key="smtp_inkluder_horinger_felt",
                                         value=st.session_state.smtp.get("inkluder_horinger", True))
    bare_favoritter_felt = st.checkbox("Bare favorittrigger", key="smtp_bare_favoritter_felt",
                                       value=st.session_state.smtp.get("bare_favoritter", False),
                                       help="Begrenser sammendraget til rader der en gjenkjent rigg er blant "
                                            "favorittriggene dine i sidepanelet.")

    e5, e6 = st.columns(2)
    if e5.button("💾 Lagre SMTP-innstillinger"):
        innstillinger = {
            "smtp_server": st.session_state.smtp_server_felt, "smtp_port": int(st.session_state.smtp_port_felt),
            "bruker": st.session_state.smtp_bruker_felt, "passord": st.session_state.smtp_passord_felt,
            "mottaker": st.session_state.smtp_mottaker_felt, "frekvens": st.session_state.smtp_frekvens_felt,
            "inkluder_bronner": inkluder_bronner_felt, "inkluder_horinger": inkluder_horinger_felt,
            "bare_favoritter": bare_favoritter_felt,
        }
        st.session_state.smtp = innstillinger
        if epost.lagre(innstillinger):
            st.success("Lagret lokalt i epost_innstillinger.json — i klartekst, i prosjektmappen din.")
        else:
            st.warning("Kunne ikke lagre til disk her. Innstillingene fungerer likevel i denne økten.")
    if e6.button("🗑️ Glem lagrede innstillinger"):
        epost.slett()
        st.session_state.smtp = dict(epost.TOMME_INNSTILLINGER)
        st.rerun()

    st.divider()
    g1, g2 = st.columns(2)
    if g1.button("📝 Generer sammendrag nå"):
        tilstand = sammendrag.last_tilstand()
        smtp = st.session_state.smtp
        resultat = sammendrag.bygg_sammendrag(
            basis, st.session_state.get("horing_siste_resultater") or [],
            st.session_state.favoritter, tilstand["sett_urls"],
            dager_fremover=op.DAGER_FREMOVER_FOR_FREKVENS.get(smtp.get("frekvens", "ukentlig"), 14),
            frekvens=smtp.get("frekvens", "ukentlig"), inkluder_bronner=smtp.get("inkluder_bronner", True),
            inkluder_horinger=smtp.get("inkluder_horinger", True), bare_favoritter=smtp.get("bare_favoritter", False),
        )
        st.session_state.sammendrag_forhandsvisning = resultat
    if "sammendrag_forhandsvisning" in st.session_state:
        forh = st.session_state.sammendrag_forhandsvisning
        st.text_area("Forhåndsvisning", value=forh["tekst"], height=300, key="sammendrag_tekst_felt")
        if g2.button("📤 Send dette sammendraget nå"):
            innstillinger = {
                "smtp_server": st.session_state.smtp_server_felt, "smtp_port": int(st.session_state.smtp_port_felt),
                "bruker": st.session_state.smtp_bruker_felt, "passord": st.session_state.smtp_passord_felt,
                "mottaker": st.session_state.smtp_mottaker_felt,
            }
            try:
                epost.send_epost(forh["emne"], forh["tekst"], innstillinger)
            except epost.EpostFeil as e:
                st.error(f"Fikk ikke sendt e-posten: {e}")
            else:
                sammendrag.lagre_tilstand(forh["denne_gang_urls"], datetime.now().isoformat())
                st.success(f"Sendt til {innstillinger['mottaker']}.")
                del st.session_state.sammendrag_forhandsvisning

# ---------- «🚀 Kom i gang»-popup ----------
# Plassert her, NEDERST i skriptet — altså etter at kartet/tabellene over allerede er bygget og
# rendret i denne kjøringen — fremfor øverst, slik at dashbordet oppleves ferdig lastet FØRST og
# popupen dukker opp over det, i stedet for å blokkere for alt annet helt i starten. Et ekte
# tidsforsinket popup («vent 30 sekunder uten klikk, så spør») er ikke noe Streamlit kan gjøre
# pålitelig (ingen bakgrunnstimer uten en ny interaksjon), så dette er den ærlige, robuste
# tilnærmingen til «etter en stund» — se oppsett_popup.py for selve beslutningslogikken.
if "oppsett_popup_vist_denne_okten" not in st.session_state:
    st.session_state.oppsett_popup_vist_denne_okten = False
if not st.session_state.oppsett_popup_vist_denne_okten:
    _bw_konfigurert = bool(bw_id and bw_hemmelig)
    _epost_konfigurert = bool(st.session_state.smtp.get("smtp_server"))
    if op.skal_vise_popup(op.last_tilstand(), _bw_konfigurert, _epost_konfigurert):
        st.session_state.oppsett_popup_vist_denne_okten = True
        _vis_oppsett_popup()