"""Sokkelradar: oversikt over boring på norsk sokkel, basert på Sodir FactMaps.

Kjør:  streamlit run app.py
"""
from __future__ import annotations

import html
from datetime import date

import folium
import pandas as pd
import streamlit as st
from streamlit_folium import st_folium

import sodir

st.set_page_config(page_title="Sokkelradar", layout="wide")

INK = "#14304a"
FARGE = {"borer": "#e2531f", "planlagt": "#1f7fb8", "ferdig": "#8494a2"}   # farger i «Status»-modus
PALETT = ["#0072B2", "#E69F00", "#009E73", "#CC79A7", "#D55E00", "#7A5195", "#56B4E9", "#8C564B", "#332288"]
ANDRE = "#9aa7b2"        # verdier utenfor de 9 vanligste
IKKE_OPPGITT = "#cfd6dc"
CACHE_SEKUNDER = 30 * 60

# Bakgrunnskart uten API-nøkkel (CARTO krever nøkkel nå og ble erstattet)
_ESRI = "https://server.arcgisonline.com/ArcGIS/rest/services"
BAKGRUNN = {
    "Lys grå": dict(tiles=f"{_ESRI}/Canvas/World_Light_Gray_Base/MapServer/tile/{{z}}/{{y}}/{{x}}",
                    attr="Tiles © Esri — Esri, DeLorme, NAVTEQ", native=16),
    "Havdyp": dict(tiles=f"{_ESRI}/Ocean/World_Ocean_Base/MapServer/tile/{{z}}/{{y}}/{{x}}",
                   attr="Tiles © Esri — GEBCO, NOAA, National Geographic, DeLorme, NAVTEQ, Esri", native=13),
    "Satellitt": dict(tiles=f"{_ESRI}/World_Imagery/MapServer/tile/{{z}}/{{y}}/{{x}}",
                      attr="Tiles © Esri — Maxar, Earthstar Geographics, and the GIS User Community", native=17),
}

# Fargelegging: navn i menyen -> kolonne i brønntabellen (None = status)
FARGEMODUS = {"Status": None, "Operatør": "operator", "Formål": "formaal_visning", "Rigg-type": "rigg_type"}
# Hvilke kolonner i tabellene som farges lyst når modusen er valgt
TABELLKOLONNER = {"Status": ("Kategori",), "Operatør": ("Operatør",), "Formål": ("Formål",),
                  "Rigg-type": ("Type", "Rigg-type")}


@st.cache_data(ttl=CACHE_SEKUNDER, show_spinner=False)
def last_bronner(fra_ar: int):
    return sodir.hent_bronner(fra_ar)


@st.cache_data(ttl=CACHE_SEKUNDER, show_spinner=False)
def last_innretninger():
    return sodir.hent_innretninger()


# ---------- Tekst og HTML ----------

def _tekst(v) -> str:
    if v is None or (not isinstance(v, str) and pd.isna(v)):
        return ""
    if isinstance(v, pd.Timestamp):
        return v.strftime("%d.%m.%Y")
    if isinstance(v, float):
        return f"{v:.0f}"
    return str(v)


def _forste(*verdier):
    """Første verdi som ikke er tom. `a or b` duger ikke: manglende verdier i pandas (NaN/NA) er ikke «falske»."""
    for v in verdier:
        if _tekst(v):
            return v
    return None


def _trygg(s: str) -> str:
    """HTML-escaper og fjerner tegn som kan bryte JavaScript-strengen folium bygger."""
    return html.escape(s).replace("`", "'").replace("${", "$ {")


def popup_html(tittel: str, rader: list[tuple[str, object]], url: object = None) -> str:
    dl = "".join(
        f"<dt style='color:#52687c'>{_trygg(k)}</dt><dd style='margin:0'>{_trygg(_tekst(v))}</dd>"
        for k, v in rader if _tekst(v)
    )
    lenke = ""
    if isinstance(url, str) and url.startswith("https://"):
        lenke = f"<a href='{_trygg(url)}' target='_blank' rel='noopener'>Åpne i Sodir FactPages</a>"
    return (
        f"<div style='font:14px sans-serif;color:{INK}'>"
        f"<div style='font:700 17px sans-serif;margin-bottom:6px'>{_trygg(tittel)}</div>"
        f"<dl style='display:grid;grid-template-columns:auto 1fr;gap:2px 12px;margin:0 0 8px'>{dl}</dl>{lenke}</div>"
    )


# ---------- Farger ----------

def lag_fargekart(serie: pd.Series) -> dict[str, str]:
    """Gir de vanligste verdiene hver sin farge. Bygges på alle innlastede brønner, slik at
    fargene ikke bytter plass når du filtrerer."""
    tell = serie.dropna().astype(str).value_counts()
    return {v: PALETT[i] for i, v in enumerate(tell.index[: len(PALETT)])}


def farge_for(verdi, kart: dict[str, str]) -> str:
    if verdi is None or (not isinstance(verdi, str) and pd.isna(verdi)):
        return IKKE_OPPGITT
    return kart.get(str(verdi), ANDRE)


def prikk(farge: str, d: int = 12, ring: bool = False, glorie: bool = False, firkant: bool = False) -> str:
    stil = f"background:#fff;border:3px solid {farge}" if ring else f"background:{farge}"
    if glorie:
        stil += f";box-shadow:0 0 0 4px {farge}33"
    radius = "2px" if firkant else "50%"
    return (f"<span style='display:inline-block;width:{d}px;height:{d}px;border-radius:{radius};"
            f"box-sizing:border-box;flex:none;{stil}'></span>")


def chip(symbol: str, tekst: str) -> str:
    return (f"<span style='display:inline-flex;align-items:center;margin:0 18px 6px 0;font:14px sans-serif;"
            f"color:{INK}'>{symbol}<span style='margin-left:7px'>{_trygg(tekst)}</span></span>")


def legende_html(synlig: pd.DataFrame, modus: str, kol: str | None, kart: dict[str, str], vis_innr: bool) -> str:
    deler = []
    if kol is None:
        antall = synlig["kategori"].value_counts()
        deler.append(chip(prikk(FARGE["borer"], 14, glorie=True), f"Borer nå ({antall.get('borer', 0)})"))
        deler.append(chip(prikk(FARGE["planlagt"], 14, ring=True), f"Tillatt eller planlagt ({antall.get('planlagt', 0)})"))
        deler.append(chip(prikk(FARGE["ferdig"], 9), f"Ferdig boret ({antall.get('ferdig', 0)})"))
    else:
        verdier = synlig[kol].map(lambda v: None if (v is None or (not isinstance(v, str) and pd.isna(v))) else str(v))
        tell = verdier.fillna("Ikke oppgitt").value_counts()
        vist_andre = 0
        for verdi, n in tell.items():
            if verdi == "Ikke oppgitt":
                deler.append(chip(prikk(IKKE_OPPGITT, 12, firkant=True), f"Ikke oppgitt ({n})"))
            elif verdi in kart:
                deler.append(chip(prikk(kart[verdi], 12, firkant=True), f"{verdi} ({n})"))
            else:
                vist_andre += n
        if vist_andre:
            deler.append(chip(prikk(ANDRE, 12, firkant=True), f"Andre ({vist_andre})"))
        deler.append("<br><span style='font:13px sans-serif;color:#52687c'>Fargen viser "
                     f"{_trygg(modus.lower())}. Formen viser status: stor sirkel med glorie = borer nå, "
                     "ring = tillatt eller planlagt, liten prikk = ferdig boret. Rigger vises som ruter med navn."
                     "</span>")
    if vis_innr:
        deler.append("<br>" if kol is None else "")
        deler.append(chip(prikk(INK, 9), "Fast innretning"))
        deler.append(chip(prikk(INK, 9, ring=True).replace("3px", "1.5px"), "Flyttbar innretning"))
    return f"<div style='margin:2px 0 8px'>{''.join(deler)}</div>"


def tabell_farget(tabell: pd.DataFrame, modus: str, kart: dict[str, str]):
    """Tinter cellene i kolonnen som tilsvarer valgt fargemodus, så tabellene matcher kartet."""
    kolonner = [k for k in TABELLKOLONNER[modus] if k in tabell.columns]
    if tabell.empty or not kolonner:
        return tabell
    if FARGEMODUS[modus] is None:
        nokler = {sodir.KATEGORI_NAVN[k]: FARGE[k] for k in FARGE}
    else:
        nokler = kart

    def tone(v):
        c = nokler.get(v) if isinstance(v, str) else None
        return f"background-color:{c}33" if c else ""

    stil = tabell.style
    return (stil.map if hasattr(stil, "map") else stil.applymap)(tone, subset=kolonner)


# ---------- Kart ----------

def rigg_ikon(navn: str, farge: str) -> folium.DivIcon:
    """Rute med navnelapp ved siden av. Bare inline-stiler, ingen ekstern CSS."""
    kode = (
        "<div style='position:relative;width:16px;height:16px'>"
        f"<div style='position:absolute;inset:1px;background:{farge};border:2px solid #fff;"
        f"outline:1.5px solid {INK};transform:rotate(45deg)'></div>"
        "<span style='position:absolute;left:22px;top:50%;transform:translateY(-50%);white-space:nowrap;"
        f"font:600 12px sans-serif;color:{INK};background:rgba(255,255,255,.93);border:1px solid #d3dce0;"
        f"padding:1px 6px'>{_trygg(navn)}</span></div>"
    )
    return folium.DivIcon(html=kode, icon_size=(16, 16), icon_anchor=(8, 8), class_name="rigg-ikon")


def bygg_kart(synlig: pd.DataFrame, innr: pd.DataFrame, vis_innr: bool, bakgrunn: str,
              kol: str | None, kart: dict[str, str]) -> folium.Map:
    b = BAKGRUNN[bakgrunn]
    m = folium.Map(location=[63, 6], zoom_start=5, tiles=None, control_scale=True)
    folium.TileLayer(tiles=b["tiles"], attr=b["attr"], name=bakgrunn, max_native_zoom=b["native"], max_zoom=18).add_to(m)

    def farge_bronn(r, kat: str) -> str:
        return FARGE[kat] if kol is None else farge_for(getattr(r, kol), kart)

    def bronn_popup(r) -> folium.Popup:
        rigg = ", ".join(x for x in (_tekst(r.rigg), _tekst(r.rigg_type)) if x)
        return folium.Popup(popup_html(_tekst(r.navn), [
            ("Kategori", sodir.KATEGORI_NAVN.get(r.kategori)), ("Status", r.status), ("Operatør", r.operator),
            ("Rigg", rigg), ("Formål", _forste(r.formaal, r.formaal_planlagt)), ("Felt", _forste(r.felt, r.funn)),
            ("Område", r.omraade), ("Lisens", r.lisens), ("Boretillatelse", r.tillatelse),
            ("Planlagt start", r.planlagt_start), ("Startet", r.start), ("Ferdig", r.ferdig),
            ("Vanndyp (m)", r.vanndyp), ("Total dybde MD (m)", r.total_dybde),
        ], r.url), max_width=330)

    # Rekkefølge = tegnerekkefølge: innretninger nederst, borende brønner øverst
    if vis_innr:
        for r in innr.itertuples():
            folium.CircleMarker(
                [r.lat, r.lon], radius=3.5, color=INK, weight=1.2, fill=True,
                fill_color="#ffffff" if r.flyttbar else INK, fill_opacity=0.85,
                tooltip=_tekst(r.navn),
                popup=folium.Popup(popup_html(_tekst(r.navn), [
                    ("Type", r.type), ("Fast eller flyttbar", r.fast_eller_flyttbar), ("Fase", r.fase),
                    ("Status", r.status), ("Operatør", r.operator), ("Vanndyp (m)", r.vanndyp),
                    ("Oppstart", r.oppstart),
                ], r.url), max_width=330),
            ).add_to(m)

    for kat in ("ferdig", "planlagt", "borer"):
        for r in synlig[synlig["kategori"] == kat].itertuples():
            pos = [r.lat, r.lon]
            farge = farge_bronn(r, kat)
            tips = f"{_tekst(r.navn)} ({_tekst(r.operator)})" if _tekst(r.operator) else _tekst(r.navn)
            if kat == "ferdig":
                stil = dict(radius=4, color="#ffffff", weight=1, fill_color=farge, fill_opacity=0.85)
            elif kat == "planlagt":
                stil = dict(radius=6.5, color=farge, weight=3, fill_color="#ffffff", fill_opacity=1)
            else:
                folium.CircleMarker(pos, radius=15, stroke=False, fill=True, fill_color=farge,
                                    fill_opacity=0.2, interactive=False).add_to(m)
                stil = dict(radius=6.5, color="#ffffff", weight=2, fill_color=farge, fill_opacity=1)
            folium.CircleMarker(pos, fill=True, tooltip=tips, popup=bronn_popup(r), **stil).add_to(m)

    # Rigger: én markør per rigg, plassert på første brønn den borer
    borer = synlig[(synlig["kategori"] == "borer") & synlig["rigg"].notna()]
    for rigg, g in borer.groupby("rigg"):
        r = next(g.itertuples())
        folium.Marker([r.lat, r.lon], icon=rigg_ikon(rigg, farge_bronn(r, "borer")),
                      tooltip=f"{rigg}: {_tekst(r.navn)}", popup=bronn_popup(r)).add_to(m)

    punkter = synlig[synlig["kategori"] == "borer"]
    if punkter.empty:
        punkter = synlig
    if not punkter.empty:
        m.fit_bounds([[float(punkter["lat"].min()), float(punkter["lon"].min())],
                      [float(punkter["lat"].max()), float(punkter["lon"].max())]],
                     padding=(60, 60), max_zoom=8)
    return m


# ---------- Sidepanel, del 1 ----------
idag = date.today()
with st.sidebar:
    st.title("Sokkelradar")
    st.caption("Boring på norsk sokkel, hentet fra Sodir.")
    fra_ar = st.selectbox("Brønner startet siden", [idag.year - 1, idag.year - 2, idag.year - 5])
    valgte = st.multiselect("Vis på kartet", list(sodir.KATEGORI_NAVN.values()),
                            default=list(sodir.KATEGORI_NAVN.values()))
    modus = st.selectbox("Fargelegg brønner etter", list(FARGEMODUS), help=(
        "Status: oransje = borer nå, blå = planlagt, grå = ferdig. De andre valgene farger etter operatør, "
        "formål eller rigg-type, og formen viser da status."))
    vis_innr = st.checkbox("Innretninger på feltet", value=True)
    bakgrunn = st.selectbox("Bakgrunnskart", list(BAKGRUNN))

# ---------- Hent data ----------
try:
    with st.spinner("Henter data fra Sodir …"):
        bronner, stat = last_bronner(fra_ar)
except Exception as e:  # nettverk, feil fra Sodir, uventet format
    st.error(f"Fikk ikke kontakt med Sodir: {e}")
    st.stop()

try:
    innr = last_innretninger()
except Exception as e:
    innr = pd.DataFrame(columns=sodir.INNR_KOLONNER)
    st.warning(f"Innretninger kunne ikke hentes ({e}). Brønnene vises likevel.")

# ---------- Sidepanel, del 2: filtre som avhenger av dataene ----------
with st.sidebar:
    operatorer = ["Alle"] + sorted(bronner["operator"].dropna().unique())
    rigger = ["Alle"] + sorted(bronner["rigg"].dropna().unique())
    operator_valg = st.selectbox("Operatør", operatorer)
    rigg_valg = st.selectbox("Rigg", rigger)
    sok = st.text_input("Søk i brønn, felt, rigg eller lisens", placeholder="For eksempel 34/10")
    if st.button("Oppdater data nå"):
        st.cache_data.clear()
        st.rerun()

# ---------- Filtrering ----------
basis = bronner
if operator_valg != "Alle":
    basis = basis[basis["operator"] == operator_valg]
if rigg_valg != "Alle":
    basis = basis[basis["rigg"] == rigg_valg]
if sok.strip() and not basis.empty:
    kolonner = ["navn", "felt", "funn", "rigg", "operator", "omraade", "lisens"]
    heystakk = basis[kolonner].fillna("").astype(str).agg(" ".join, axis=1).str.lower()
    basis = basis[heystakk.str.contains(sok.strip().lower(), regex=False)]
kategorier = [k for k, navn in sodir.KATEGORI_NAVN.items() if navn in valgte]
synlig = basis[basis["kategori"].isin(kategorier)]

fargekol = FARGEMODUS[modus]
fargekart = lag_fargekart(bronner[fargekol]) if fargekol else {}

# ---------- Hovedvisning ----------
antall = basis["kategori"].value_counts()
c1, c2, c3 = st.columns(3)
c1.metric(sodir.KATEGORI_NAVN["borer"], int(antall.get("borer", 0)))
c2.metric(sodir.KATEGORI_NAVN["planlagt"], int(antall.get("planlagt", 0)))
c3.metric(sodir.KATEGORI_NAVN["ferdig"], int(antall.get("ferdig", 0)))

kart_kol, liste_kol = st.columns([3, 2])
with kart_kol:
    st.markdown(legende_html(synlig, modus, fargekol, fargekart, vis_innr), unsafe_allow_html=True)
    st_folium(bygg_kart(synlig, innr, vis_innr, bakgrunn, fargekol, fargekart),
              height=680, returned_objects=[], key="kart")

with liste_kol:
    fane1, fane2, fane3, fane4 = st.tabs(["Rigger som borer nå", "Tillatt eller planlagt", "Per operatør", "Alle brønner"])
    with fane1:
        sorter = st.selectbox("Sorter etter", sodir.RIGG_SORTERING, key="sorter_rigg")
        tabell = sodir.rigger_som_borer(synlig, sorter)
        if tabell.empty:
            st.write("Ingen brønner har status «borer» med disse filtrene.")
        else:
            st.dataframe(tabell_farget(tabell, modus, fargekart), hide_index=True)
    with fane2:
        sorter = st.selectbox("Sorter etter", sodir.PLAN_SORTERING, key="sorter_plan")
        tabell = sodir.planlagte(synlig, sorter)
        if tabell.empty:
            st.write("Ingen tillatte eller planlagte brønner med disse filtrene.")
        else:
            st.dataframe(tabell_farget(tabell, modus, fargekart), hide_index=True)
            st.caption("«Dager til start» er negativt når planlagt dato er passert.")
    with fane3:
        tabell = sodir.per_operator(basis)
        st.dataframe(tabell_farget(tabell, modus, fargekart), hide_index=True)
        st.caption("Alle brønner som passer filtrene, uavhengig av hvilke kategorier som vises på kartet.")
    with fane4:
        tabell = sodir.alle_bronner(synlig)
        st.dataframe(tabell_farget(tabell, modus, fargekart), hide_index=True)
        st.caption("Klikk på en kolonneoverskrift for å sortere.")
        st.download_button(
            "Last ned som CSV (åpnes direkte i norsk Excel)",
            data=tabell.to_csv(index=False, sep=";", decimal=",").encode("utf-8-sig"),
            file_name=f"sokkelradar_bronner_{idag.isoformat()}.csv", mime="text/csv",
        )

with st.expander("Om dataene"):
    st.write(
        f"{len(bronner)} brønner er tolket av {stat['hentet']} hentet fra Sodir. "
        f"{stat['ukjent']} manglet startdato, planlagt start og boretillatelse, "
        f"{stat['aldri']} har status «aldri boret», og {stat['uten_posisjon']} manglet posisjon."
    )
    st.caption("Sodir har ikke et eget «borer nå»-felt, så kategoriene er tolket fra status og datoer. "
               "Tabellen under viser hvilke rå statusverdier som havnet i hvilken kategori.")
    st.dataframe(stat["statusverdier"], hide_index=True)
    st.caption("Posisjoner er omtrentlige (0–300 m) og skal ikke brukes til navigasjon. Data: Sodir, lisens NLOD.")