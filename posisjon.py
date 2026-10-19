"""Posisjon og kart: farger, popup-tekst og selve kartbyggingen.

Alt som handler om HVOR noe vises og HVORDAN det ser ut, bor her — ingen
HTTP-kall (hent_data.py) og ingen tabell-logikk (databehandling.py).
"""
from __future__ import annotations

import html
import re

import folium
import pandas as pd
from folium.plugins import MarkerCluster

import databehandling as db
import destinasjon as dest

INK = "#14304a"
# Farger i «Status»-modus. «annet» (tidlig fase/uavklart) er lilla og stiplet — fortsatt verdt å
# følge med på. «avlyst» er lys grå og enda mer dempet enn «ferdig», siden den er reelt irrelevant
# for drift. Se databehandling.klassifiser for hvordan en brønn havner i hvilken kategori.
FARGE = {"borer": "#e2531f", "planlagt": "#1f7fb8", "annet": "#7a5195", "avlyst": "#b7bec5", "ferdig": "#8494a2"}
PALETT = ["#0072B2", "#E69F00", "#009E73", "#CC79A7", "#D55E00", "#7A5195", "#56B4E9", "#8C564B", "#332288"]
ANDRE = "#9aa7b2"        # verdier utenfor de 9 vanligste
IKKE_OPPGITT = "#cfd6dc"
AIS_FARGE = "#d7263d"    # live AIS-posisjon fra BarentsWatch — egen farge, uavhengig av fargemodus

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


def legende_html(synlig: pd.DataFrame, modus: str, kol: str | None, kart: dict[str, str], vis_innr: bool,
                 ais_antall: int = 0) -> str:
    deler = []
    if kol is None:
        antall = synlig["kategori"].value_counts()
        deler.append(chip(prikk(FARGE["borer"], 14, glorie=True), f"Borer nå ({antall.get('borer', 0)})"))
        deler.append(chip(prikk(FARGE["planlagt"], 14, ring=True), f"Tillatt eller planlagt ({antall.get('planlagt', 0)})"))
        if antall.get("annet", 0) > 0:
            deler.append(chip(prikk(FARGE["annet"], 12, firkant=True), f"Annet / uavklart ({antall.get('annet', 0)})"))
        if antall.get("avlyst", 0) > 0:
            deler.append(chip(prikk(FARGE["avlyst"], 10, firkant=True), f"Avlyst ({antall.get('avlyst', 0)})"))
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
                     "ring = tillatt eller planlagt, stiplet firkant = annet/usikkert, liten prikk = ferdig boret. "
                     "Rigger vises som ruter med navn.</span>")
    if vis_innr:
        deler.append("<br>" if kol is None else "")
        deler.append(chip(prikk(INK, 9), "Fast innretning"))
        deler.append(chip(prikk(INK, 9, ring=True).replace("3px", "1.5px"), "Flyttbar innretning"))
    if ais_antall:
        deler.append("<br>")
        ais_symbol = (f"<span style='display:inline-block;width:10px;height:10px;border-radius:50%;"
                     f"background:{AIS_FARGE};border:1.5px solid #fff;box-shadow:0 0 0 3px {AIS_FARGE}55'></span>")
        deler.append(chip(ais_symbol, f"Live AIS-posisjon, BarentsWatch ({ais_antall})"))
        deler.append("<br><span style='font:13px sans-serif;color:#52687c'>AIS-markører viser forkortet navn "
                     "(full navn i boblen du klikker på), farges etter riggens kategori, og grupperes i et tall "
                     "til du zoomer nærmere. Stiplet linje = oppgitt AIS-destinasjon, ikke en beregnet rute.</span>")
    return f"<div style='margin:2px 0 8px'>{''.join(deler)}</div>"


def tabell_farget(tabell: pd.DataFrame, modus: str, kart: dict[str, str]):
    """Tinter cellene i kolonnen som tilsvarer valgt fargemodus, så tabellene matcher kartet."""
    kolonner = [k for k in TABELLKOLONNER[modus] if k in tabell.columns]
    if tabell.empty or not kolonner:
        return tabell
    if FARGEMODUS[modus] is None:
        nokler = {db.KATEGORI_NAVN[k]: FARGE[k] for k in FARGE}
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


def _forkortelse(navn: str) -> str:
    """Korte etiketter på AIS-markøren (full navn er likevel i tooltip/popup) — ellers
    blir kartet uleselig når flere AIS-markører ligger nær hverandre ved utzooming."""
    deler = re.findall(r"[A-ZÆØÅ0-9]+", str(navn).upper())
    if not deler:
        return str(navn)[:4].upper()
    if len(deler) == 1:
        return deler[0][:4]
    return "".join(d[0] for d in deler[:4])


def ais_ikon(navn: str, farge: str = AIS_FARGE) -> folium.DivIcon:
    """Pulserende prikk for en live AIS-posisjon — trekant-spiss og forkortet navn holder den
    visuelt adskilt fra rigg-ruten (rigg_ikon), som viser Sodir sin (statiske) brønnposisjon.
    Farges etter riggens kategori (borer/planlagt/annet/ferdig) når den er kjent, ellers AIS_FARGE."""
    kort = _trygg(_forkortelse(navn))
    kode = (
        "<div style='position:relative;width:14px;height:14px'>"
        f"<span style='position:absolute;inset:0;border-radius:50%;background:{farge};"
        "opacity:.35;animation:aispuls 1.8s infinite'></span>"
        f"<span style='position:absolute;inset:3px;border-radius:50%;background:{farge};"
        "border:1.5px solid #fff'></span>"
        "<span style='position:absolute;left:20px;top:50%;transform:translateY(-50%);white-space:nowrap;"
        f"font:700 11px sans-serif;color:{farge};background:rgba(255,255,255,.93);border:1px solid "
        f"{farge}66;padding:1px 6px;border-radius:3px'>▲ {kort}</span>"
        "<style>@keyframes aispuls{0%{transform:scale(1);opacity:.35}70%{transform:scale(2.4);opacity:0}"
        "100%{opacity:0}}</style></div>"
    )
    return folium.DivIcon(html=kode, icon_size=(14, 14), icon_anchor=(7, 7), class_name="ais-ikon")


def ais_popup(rigg: str, p: dict) -> folium.Popup:
    treff = "Eksakt navnetreff" if p.get("match") == "eksakt" else "Delvis navnetreff — sjekk selv at det stemmer"
    rader = [
        ("Fartøynavn (AIS)", p.get("navn")), ("MMSI", p.get("mmsi")),
        ("Fart (knop)", p.get("fart")), ("Kurs", p.get("kurs")), ("Sist meldt", p.get("tid")),
        ("Kobling", treff),
    ]
    if p.get("destinasjon"):
        rader.append(("Oppgitt destinasjon", p["destinasjon"]))
    if p.get("eta"):
        rader.append(("Oppgitt ETA", p["eta"]))
    if p.get("destinasjon") or p.get("eta"):
        rader.append(("OBS", "Skrevet inn av mannskapet selv — ofte forkortet/utdatert, ikke en beregnet rute"))
    return folium.Popup(popup_html(f"{rigg} — live AIS", rader), max_width=320)


def destinasjon_vektor(fra: list[float], til: tuple[float, float], farge: str = AIS_FARGE) -> folium.PolyLine:
    """Rett linje (ikke en faktisk rute) fra riggens AIS-posisjon til en gjenkjent destinasjon.
    Tykk og godt synlig med vilje — en stiplet linje med 2 px strek og 10 px luke (forrige
    versjon) var nesten usynlig på et lyst bakgrunnskart."""
    return folium.PolyLine([fra, list(til)], color=farge, weight=4, opacity=0.9, dash_array="10,8")


def destinasjon_markor(til: tuple[float, float], tekst: str, farge: str) -> folium.CircleMarker:
    """Liten ring ved enden av destinasjonslinjen, så det er tydelig HVOR den peker, selv om
    endepunktet ligger langt unna og i utgangspunktet er utenfor synlig kartutsnitt."""
    return folium.CircleMarker(
        list(til), radius=8, color=farge, weight=3, fill=True, fill_color="#ffffff", fill_opacity=0.9,
        tooltip=f"Oppgitt destinasjon: {tekst}",
    )


def legg_til_ais(m: folium.Map, ais: dict[str, dict], steder: dict[str, tuple[float, float]] | None = None) -> None:
    """Tegner én markør per rigg med AIS-treff, gruppert med MarkerCluster slik at kartet ikke
    blir uleselig ved utzooming (klyngene løses opp igjen fra og med zoom-nivå 7). Legges til
    sist, så de havner øverst i kartet, adskilt fra (og oppå) Sodir sin statiske brønn/rigg-posisjon.
    Når riggens oppgitte AIS-destinasjon trygt kan tolkes som et kjent sted (se destinasjon.py),
    tegnes i tillegg en retningsvisende linje dit."""
    if not ais:
        return
    klynge = MarkerCluster(name="Live AIS", disableClusteringAtZoom=7, maxClusterRadius=40).add_to(m)
    for rigg, p in ais.items():
        if p.get("lat") is None or p.get("lon") is None:
            continue
        farge = FARGE.get(p.get("kategori"), AIS_FARGE)
        folium.Marker(
            [p["lat"], p["lon"]], icon=ais_ikon(rigg, farge), popup=ais_popup(rigg, p),
            tooltip=f"{rigg}: live posisjon (AIS)",
        ).add_to(klynge)
        if steder is not None:
            til = dest.finn_posisjon(p.get("destinasjon"), steder)
            if til:
                destinasjon_vektor([p["lat"], p["lon"]], til, farge).add_to(m)
                destinasjon_markor(til, p.get("destinasjon") or "", farge).add_to(m)


def bygg_mini_kart(
    fra: tuple[float, float], til: tuple[float, float], fra_navn: str, til_navn: str,
    bakgrunn: str = "Lys grå",
) -> folium.Map:
    """Et lite, SELVSTENDIG kart (egen Map-instans, uavhengig av hovedkartet i bygg_kart()) med
    bare to markører og en linje mellom dem — lagd for Høringer-fanen, for å vise en riggs vei fra
    nåværende AIS-posisjon til et felt nevnt i en høring, FØR mannskapet nødvendigvis har
    oppdatert sin egen AIS-destinasjon til det. Samme visuelle språk (farge, stiplet linje) som
    destinasjon_vektor()/destinasjon_markor() på hovedkartet, for gjenkjennelse — «stiplet linje =
    rett linje, ikke en beregnet seilingsrute» gjelder her akkurat som der."""
    b = BAKGRUNN.get(bakgrunn, next(iter(BAKGRUNN.values())))
    midt = [(fra[0] + til[0]) / 2, (fra[1] + til[1]) / 2]
    m = folium.Map(location=midt, zoom_start=5, tiles=None, control_scale=True)
    folium.TileLayer(tiles=b["tiles"], attr=b["attr"], max_native_zoom=b["native"], max_zoom=18).add_to(m)
    folium.CircleMarker(
        list(fra), radius=7, color=AIS_FARGE, weight=2, fill=True, fill_color=AIS_FARGE, fill_opacity=0.9,
        tooltip=f"{fra_navn}: nåværende AIS-posisjon",
    ).add_to(m)
    destinasjon_vektor(list(fra), til, AIS_FARGE).add_to(m)
    destinasjon_markor(til, til_navn, AIS_FARGE).add_to(m)
    m.fit_bounds([list(fra), list(til)], padding=(40, 40))
    return m


def bygg_kart(synlig: pd.DataFrame, innr: pd.DataFrame, vis_innr: bool, bakgrunn: str,
              kol: str | None, kart: dict[str, str], ais: dict[str, dict] | None = None,
              steder: dict[str, tuple[float, float]] | None = None) -> folium.Map:
    b = BAKGRUNN[bakgrunn]
    m = folium.Map(location=[63, 6], zoom_start=5, tiles=None, control_scale=True)
    folium.TileLayer(tiles=b["tiles"], attr=b["attr"], name=bakgrunn, max_native_zoom=b["native"], max_zoom=18).add_to(m)

    def farge_bronn(r, kat: str) -> str:
        return FARGE[kat] if kol is None else farge_for(getattr(r, kol), kart)

    def bronn_popup(r) -> folium.Popup:
        rigg = ", ".join(x for x in (_tekst(r.rigg), _tekst(r.rigg_type)) if x)
        return folium.Popup(popup_html(_tekst(r.navn), [
            ("Kategori", db.KATEGORI_NAVN.get(r.kategori)), ("Status", r.status), ("Operatør", r.operator),
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

    for kat in ("avlyst", "ferdig", "annet", "planlagt", "borer"):
        for r in synlig[synlig["kategori"] == kat].itertuples():
            pos = [r.lat, r.lon]
            farge = farge_bronn(r, kat)
            tips = f"{_tekst(r.navn)} ({_tekst(r.operator)})" if _tekst(r.operator) else _tekst(r.navn)
            if kat == "avlyst":
                stil = dict(radius=3.5, color=farge, weight=1, fill_color="#ffffff", fill_opacity=0.6,
                            dash_array="1,4")
            elif kat == "ferdig":
                stil = dict(radius=4, color="#ffffff", weight=1, fill_color=farge, fill_opacity=0.85)
            elif kat == "annet":
                stil = dict(radius=5, color=farge, weight=2, fill_color="#ffffff", fill_opacity=0.9,
                            dash_array="3,3")
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

    if ais:
        legg_til_ais(m, ais, steder)

    punkter = synlig[synlig["kategori"] == "borer"]
    if punkter.empty:
        punkter = synlig
    if not punkter.empty:
        m.fit_bounds([[float(punkter["lat"].min()), float(punkter["lon"].min())],
                      [float(punkter["lat"].max()), float(punkter["lon"].max())]],
                     padding=(60, 60), max_zoom=8)
    return m