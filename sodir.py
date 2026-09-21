"""Henting og tolking av data fra Sodir FactMaps (åpent API, ingen innlogging).

Modulen bruker bare requests og pandas, slik at den kan gjenbrukes i andre
skript (varsler, AIS-kobling, eksport) uten å dra inn Streamlit.
"""
from __future__ import annotations

import math

import pandas as pd
import requests

BASE = "https://factmaps.sodir.no/api/rest/services/Factmaps/FactMapsWGS84/MapServer"
BRONN_LAG = 201      # "Wellbores, all"
INNR_LAG = 304       # "Facilities, in place"
SIDE = 1000          # MaxRecordCount hos Sodir
TIMEOUT = (10, 90)   # sekunder: tilkobling, lesing

KATEGORI_NAVN = {
    "borer": "Borer nå",
    "planlagt": "Tillatt eller planlagt",
    "ferdig": "Ferdig boret",
}

# Sodir-felt -> kolonnenavn vi bruker videre
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


def til_dato(serie: pd.Series) -> pd.Series:
    """Datoer fra Sodir kommer som epoch-millisekunder eller ISO-tekst. Begge håndteres."""
    tall = pd.to_numeric(serie, errors="coerce")
    fra_tall = pd.to_datetime(tall, unit="ms", errors="coerce")
    tekst = serie.where(tall.isna())
    fra_tekst = pd.to_datetime(tekst, errors="coerce", utc=True).dt.tz_localize(None)
    return fra_tall.fillna(fra_tekst)


def til_tabell(features: list[dict], felt_map: dict[str, str]) -> tuple[pd.DataFrame, int]:
    """GeoJSON-features -> DataFrame med kolonnene i felt_map pluss lon og lat.

    Returnerer også antall features som manglet gyldig posisjon."""
    rader = []
    for f in features:
        g = f.get("geometry") or {}
        c = g.get("coordinates")
        if g.get("type") != "Point" or not c or len(c) < 2:
            continue
        try:
            lon, lat = float(c[0]), float(c[1])
        except (TypeError, ValueError):
            continue
        if not (math.isfinite(lon) and math.isfinite(lat)) or (lon == 0 and lat == 0):
            continue
        rad = dict(f.get("properties") or {})
        rad["lon"], rad["lat"] = lon, lat
        rader.append(rad)
    df = pd.DataFrame(rader).reindex(columns=list(felt_map) + ["lon", "lat"])
    return df.rename(columns=felt_map), len(features) - len(rader)


def klassifiser(df: pd.DataFrame) -> pd.Series:
    """Sodir har ikke et eget «borer nå»-felt, så vi tolker status og datoer.

    borer     status inneholder DRILLING
    ferdig    har startdato og borer ikke lenger
    planlagt  ingen startdato, men planlagt start eller boretillatelse finnes
    aldri     status sier at brønnen aldri skal bores (filtreres bort)
    ukjent    ingen startdato, planlagt start eller tillatelse (filtreres bort)
    """
    status = df["status"].fillna("").astype(str).str.upper()
    kat = pd.Series("ukjent", index=df.index, dtype="object")
    har_plan = df["planlagt_start"].notna() | df["tillatelse"].notna()
    kat.loc[df["start"].isna() & har_plan] = "planlagt"
    kat.loc[df["start"].notna()] = "ferdig"
    kat.loc[status.str.contains("DRILLING", regex=False)] = "borer"
    kat.loc[status.str.contains("NEVER", regex=False)] = "aldri"
    return kat


def tolk_bronner(features: list[dict]) -> tuple[pd.DataFrame, dict]:
    """Gjør rå features om til en ryddig brønntabell og en statistikk-ordbok."""
    df, uten_pos = til_tabell(features, BRONN_FELT)
    for kol in ("start", "ferdig", "planlagt_start", "tillatelse"):
        df[kol] = til_dato(df[kol])
    df["kategori"] = klassifiser(df)
    df["formaal_visning"] = df["formaal"].fillna(df["formaal_planlagt"])
    df["dager"] = (pd.Timestamp.now() - df["start"]).dt.days.astype("Int64")

    stat = {
        "hentet": len(features),
        "uten_posisjon": uten_pos,
        "ukjent": int((df["kategori"] == "ukjent").sum()),
        "aldri": int((df["kategori"] == "aldri").sum()),
    }
    df = df[df["kategori"].isin(KATEGORI_NAVN)].reset_index(drop=True)

    tell = (
        df.assign(status=df["status"].fillna("(tom)"))
        .groupby(["kategori", "status"]).size().reset_index(name="Antall")
        .sort_values("Antall", ascending=False)
    )
    tell["Kategori"] = tell["kategori"].map(KATEGORI_NAVN)
    stat["statusverdier"] = tell.rename(columns={"status": "Status"})[["Status", "Kategori", "Antall"]]
    return df, stat


def hent_bronner(fra_ar: int) -> tuple[pd.DataFrame, dict]:
    """Brønner startet fra og med fra_ar, pluss brønner uten startdato (tillatt/planlagt)."""
    where = f"wlbEntryYear >= {int(fra_ar)} OR wlbEntryDate IS NULL"
    return tolk_bronner(hent_lag(BRONN_LAG, where, ",".join(BRONN_FELT)))


def tolk_innretninger(features: list[dict]) -> pd.DataFrame:
    df, _ = til_tabell(features, INNR_FELT)
    df["oppstart"] = til_dato(df["oppstart"])
    df["flyttbar"] = df["fast_eller_flyttbar"].fillna("").astype(str).str.upper().str.contains("MOV", regex=False)
    return df.reset_index(drop=True)


def hent_innretninger() -> pd.DataFrame:
    """Innretninger over vann som er på plass (faste og flyttbare)."""
    return tolk_innretninger(hent_lag(INNR_LAG, "fclSurface = 'Y'", ",".join(INNR_FELT)))


RIGG_SORTERING = ["Rigg (A–Å)", "Operatør", "Lengst i drift først"]
PLAN_SORTERING = ["Planlagt start", "Operatør", "Område"]
_REKKEFOLGE = {"borer": 0, "planlagt": 1, "ferdig": 2}


def _dato_tekst(serie: pd.Series) -> pd.Series:
    return serie.dt.strftime("%d.%m.%Y").fillna("")


def rigger_som_borer(df: pd.DataFrame, sorter: str = "Rigg (A–Å)") -> pd.DataFrame:
    """Én rad per rigg med brønn(er), operatør og antall dager siden oppstart."""
    kolonner = ["Rigg", "Type", "Brønn", "Operatør", "Dager"]
    b = df[df["kategori"] == "borer"]
    if b.empty:
        return pd.DataFrame(columns=kolonner)
    b = b.assign(rigg=b["rigg"].fillna("Rigg ikke oppgitt"))
    rader = []
    for rigg, g in b.groupby("rigg", sort=True):
        rader.append({
            "Rigg": rigg,
            "Type": ", ".join(sorted(set(g["rigg_type"].dropna()))),
            "Brønn": ", ".join(g["navn"].dropna()),
            "Operatør": ", ".join(sorted(set(g["operator"].dropna()))),
            "Dager": g["dager"].max(),
        })
    tab = pd.DataFrame(rader, columns=kolonner)
    if sorter == "Operatør":
        tab = tab.sort_values(["Operatør", "Rigg"])
    elif sorter == "Lengst i drift først":
        tab = tab.sort_values(["Dager", "Rigg"], ascending=[False, True], na_position="last")
    else:
        tab = tab.sort_values("Rigg")
    return tab.reset_index(drop=True)


def planlagte(df: pd.DataFrame, sorter: str = "Planlagt start") -> pd.DataFrame:
    """Tillatte eller planlagte brønner. «Dager til start» er negativt hvis planlagt dato er passert."""
    p = df[df["kategori"] == "planlagt"].copy()
    p["_dato"] = p["planlagt_start"].fillna(p["tillatelse"])
    if sorter == "Operatør":
        p = p.sort_values(["operator", "_dato"], na_position="last")
    elif sorter == "Område":
        p = p.sort_values(["omraade", "_dato"], na_position="last")
    else:
        p = p.sort_values("_dato", na_position="last")
    til_start = (p["planlagt_start"] - pd.Timestamp.now().normalize()).dt.days.astype("Int64")
    return pd.DataFrame({
        "Brønn": p["navn"],
        "Operatør": p["operator"],
        "Formål": p["formaal_visning"],
        "Planlagt start": _dato_tekst(p["planlagt_start"]),
        "Dager til start": til_start,
        "Tillatt": _dato_tekst(p["tillatelse"]),
        "Område": p["omraade"],
        "Felt": p["felt"].fillna(p["funn"]),
    }).reset_index(drop=True)


def per_operator(df: pd.DataFrame) -> pd.DataFrame:
    """Antall brønner per operatør og kategori, med mest aktive operatører øverst."""
    navn = [KATEGORI_NAVN[k] for k in KATEGORI_NAVN]
    if df.empty:
        return pd.DataFrame(columns=["Operatør"] + navn)
    t = pd.crosstab(df["operator"].fillna("Ikke oppgitt"), df["kategori"])
    t = t.reindex(columns=list(KATEGORI_NAVN), fill_value=0)
    t.columns = navn
    t.index.name = "Operatør"
    return t.sort_values(navn, ascending=False).reset_index()


def alle_bronner(df: pd.DataFrame) -> pd.DataFrame:
    """Lesbar tabell over alle synlige brønner: borende først, deretter planlagte, deretter ferdige (nyeste først)."""
    d = df.assign(_k=df["kategori"].map(_REKKEFOLGE)).sort_values(
        ["_k", "start"], ascending=[True, False], na_position="last")
    return pd.DataFrame({
        "Brønn": d["navn"],
        "Kategori": d["kategori"].map(KATEGORI_NAVN),
        "Status": d["status"],
        "Operatør": d["operator"],
        "Rigg": d["rigg"],
        "Rigg-type": d["rigg_type"],
        "Formål": d["formaal_visning"],
        "Felt": d["felt"].fillna(d["funn"]),
        "Område": d["omraade"],
        "Lisens": d["lisens"],
        "Boretillatelse": _dato_tekst(d["tillatelse"]),
        "Planlagt start": _dato_tekst(d["planlagt_start"]),
        "Startet": _dato_tekst(d["start"]),
        "Ferdig": _dato_tekst(d["ferdig"]),
        "Vanndyp (m)": d["vanndyp"],
        "Lat": d["lat"],
        "Lon": d["lon"],
    }).reset_index(drop=True)