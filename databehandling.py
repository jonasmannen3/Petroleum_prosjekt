"""Databehandling: gjør rå Sodir-features om til ryddige tabeller.

Ingen HTTP-kall her (se hent_data.py) og ingen kart eller farger (se
posisjon.py) — bare pandas. Kan dermed testes og gjenbrukes for seg.
"""
from __future__ import annotations

import math

import pandas as pd

KATEGORI_NAVN = {
    "borer": "Borer nå",
    "planlagt": "Tillatt eller planlagt",
    "annet": "Annet / uavklart (tidlig fase)",
    "avlyst": "Avlyst",
    "ferdig": "Ferdig boret",
}
# Tegnerekkefølge og sortering i «Alle brønner»: det mest aktuelle for drift i lower completion
# øverst (borer, så planlagt, så det usikre "annet"), avlyst og ferdig boret sist (begge er «ikke
# lenger i spill», men avlyst er mest irrelevant av de to).
_REKKEFOLGE = {"borer": 0, "planlagt": 1, "annet": 2, "avlyst": 3, "ferdig": 4}

RIGG_SORTERING = ["Rigg (A–Å)", "Operatør", "Lengst i drift først"]
PLAN_SORTERING = ["Planlagt start", "Operatør", "Område"]


def til_dato(serie: pd.Series) -> pd.Series:
    """Datoer fra Sodir kommer som epoch-millisekunder eller ISO-tekst. Begge håndteres.

    Noen (særlig eldre) rader har tall langt utenfor et gyldig datointervall — feilregistrering
    eller en sentinel-verdi. Disse nullstilles FØR konvertering: lar man dem stå, overflower
    numpy sin interne millisekund→nanosekund-multiplikasjon int64 og kaster «overflow
    encountered in multiply» i stedet for at errors="coerce" får gjøre jobben sin."""
    tall = pd.to_numeric(serie, errors="coerce")
    # Gyldig vindu: ca. år 1900–2200 i epoch-millisekunder — godt innenfor pandas sin egen
    # Timestamp-grense (ca. 1677–2262), så selve multiplikasjonen med nanosekund-faktoren
    # overflower aldri for tall som overlever dette filteret.
    GYLDIG_MIN, GYLDIG_MAX = -2_208_988_800_000, 7_258_118_400_000
    tall = tall.where((tall >= GYLDIG_MIN) & (tall <= GYLDIG_MAX))
    try:
        fra_tall = pd.to_datetime(tall, unit="ms", errors="coerce")
    except (OverflowError, ValueError):
        fra_tall = pd.Series(pd.NaT, index=serie.index)
    tekst = serie.where(tall.isna())
    try:
        fra_tekst = pd.to_datetime(tekst, errors="coerce", utc=True).dt.tz_localize(None)
    except (OverflowError, ValueError):
        fra_tekst = pd.Series(pd.NaT, index=serie.index)
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
    annet     verken startdato, planlagt start eller tillatelse, og status sier IKKE at den er
              avlyst. Dette er brønner i tidlig fase (lisens/brønn registrert i Sodir, men ingen
              dato satt ennå) — potensielt relevante å følge med på, ikke «ikke relevant».
    avlyst    status sier eksplisitt at brønnen aldri skal bores («NEVER …»). Skilt fra «annet»
              nettopp for at du skal kunne skru den helt av uten å miste det reelt uavklarte.
    """
    status = df["status"].fillna("").astype(str).str.upper()
    kat = pd.Series("annet", index=df.index, dtype="object")
    har_plan = df["planlagt_start"].notna() | df["tillatelse"].notna()
    kat.loc[df["start"].isna() & har_plan] = "planlagt"
    kat.loc[df["start"].notna()] = "ferdig"
    kat.loc[status.str.contains("DRILLING", regex=False)] = "borer"
    kat.loc[status.str.contains("NEVER", regex=False)] = "avlyst"  # eksplisitt avlyst, uansett datofelt
    return kat


# Sodir sitt «wellbore»-API-lag brukes OGSÅ for rene geotekniske grunnundersøkelser (boring for å
# sjekke sjøbunnens grunnforhold før en installasjon/fundament settes ned) — ikke bare ekte
# leting/produksjon av olje/gass. Disse har «formål: SOIL DRILLING» og dukket opp i «Annet /
# uavklart» uten dato/status (fordi de aldri FÅR en planlagt_start/tillatelse i vanlig forstand),
# og ble feilaktig lest som en stor bunke «kommende» aktivitet. Filtreres bort tidlig, FØR
# kategorisering, så de aldri dukker opp i noen fane eller noe av tallverket.
GEOTEKNISK_FORMAL = {"SOIL DRILLING"}


def tolk_bronner(features: list[dict], felt_map: dict[str, str]) -> tuple[pd.DataFrame, dict]:
    """Gjør rå features om til en ryddig brønntabell og en statistikk-ordbok.

    felt_map kommer fra hent_data.BRONN_FELT (overføres eksplisitt så denne filen
    ikke trenger å importere nettverksmodulen)."""
    df, uten_pos = til_tabell(features, felt_map)
    formaal_rått = df["formaal"].fillna(df["formaal_planlagt"]).fillna("").astype(str).str.upper().str.strip()
    er_geoteknisk = formaal_rått.isin(GEOTEKNISK_FORMAL)
    geoteknisk_antall = int(er_geoteknisk.sum())
    df = df[~er_geoteknisk].reset_index(drop=True)
    for kol in ("start", "ferdig", "planlagt_start", "tillatelse"):
        df[kol] = til_dato(df[kol])
    df["kategori"] = klassifiser(df)
    df["formaal_visning"] = df["formaal"].fillna(df["formaal_planlagt"])
    df["dager"] = (pd.Timestamp.now() - df["start"]).dt.days.astype("Int64")

    stat = {"hentet": len(features), "uten_posisjon": uten_pos, "geoteknisk_boring": geoteknisk_antall,
            "annet": int((df["kategori"] == "annet").sum()), "avlyst": int((df["kategori"] == "avlyst").sum())}

    tell = (
        df.assign(status=df["status"].fillna("(tom)"))
        .groupby(["kategori", "status"]).size().reset_index(name="Antall")
        .sort_values("Antall", ascending=False)
    )
    tell["Kategori"] = tell["kategori"].map(KATEGORI_NAVN)
    stat["statusverdier"] = tell.rename(columns={"status": "Status"})[["Status", "Kategori", "Antall"]]
    return df.reset_index(drop=True), stat


def tolk_innretninger(features: list[dict], felt_map: dict[str, str]) -> pd.DataFrame:
    df, _ = til_tabell(features, felt_map)
    df["oppstart"] = til_dato(df["oppstart"])
    df["flyttbar"] = df["fast_eller_flyttbar"].fillna("").astype(str).str.upper().str.contains("MOV", regex=False)
    return df.reset_index(drop=True)


def _dato_tekst(serie: pd.Series) -> pd.Series:
    return serie.dt.strftime("%d.%m.%Y").fillna("")


# Hvor mange dager en «planlagt»-kategorisert brønn kan være forbi sin egen planlagt-start-/
# tillatelse-dato før den merkes som trolig forsinket/stillestående. Sodir har ikke noe eget felt
# for dette — brønnen blir bare liggende i kategorien «planlagt» til noen oppdaterer status (start,
# ferdig, eller avlyst) i registeret, uansett hvor lenge siden den opprinnelige datoen var. 180
# dager (et halvt år) er et bevisst romslig anslag: offshore-prosjekter forskyves ofte uten at det
# betyr noe er galt, så terskelen skal fange det som virker reelt stillestående, ikke normal slark.
GRENSE_FORSINKET_DAGER = -180


def _forsinket_merke(dager_til: int | float | None) -> str:
    """Et lite, forklarlig varsel-ikon for rader der «dager til»-tallet tyder på at brønnen har
    ligget i «planlagt»-kategorien lenge etter sin egen dato — se GRENSE_FORSINKET_DAGER over.
    ALDRI et gjettet nytt status (vi vet ikke OM den faktisk er lagt på is), bare en synlig
    oppfordring til å dobbeltsjekke selv, i stedet for et tall som stille ser ut som alle andre."""
    if dager_til is None or (isinstance(dager_til, float) and pd.isna(dager_til)):
        return ""
    return "⚠️ Trolig forsinket/stillestående" if dager_til <= GRENSE_FORSINKET_DAGER else ""


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
    # Merket under bruker «_dato» (planlagt_start, eller i mangel av det tillatelse-datoen), IKKE
    # bare «Dager til start»-kolonnen over — en brønn med KUN en gammel boretillatelse og ingen
    # planlagt_start ville ellers aldri bli flagget, selv om den er like stillestående.
    dager_for_merke = (p["_dato"] - pd.Timestamp.now().normalize()).dt.days
    return pd.DataFrame({
        "Brønn": p["navn"],
        "Operatør": p["operator"],
        "Formål": p["formaal_visning"],
        "Planlagt start": _dato_tekst(p["planlagt_start"]),
        "Dager til start": til_start,
        "Tillatt": _dato_tekst(p["tillatelse"]),
        "Område": p["omraade"],
        "Felt": p["felt"].fillna(p["funn"]),
        "Merknad": dager_for_merke.map(_forsinket_merke),
    }).reset_index(drop=True)


def rigger_planlagt(df: pd.DataFrame) -> pd.DataFrame:
    """Én rad per rigg for brønner som er tillatt eller planlagt, men ikke påbegynt (ikke «entered»).

    Rigg er ofte ikke satt før kontrahering er kjent, så «Rigg ikke tildelt» er en egen gruppe,
    ikke en feil. Sortert på nærmeste planlagte start eller boretillatelse.

    FORSØKTE TIDLIGERE å også ta med «annet»-kategorien (tidlig fase/uavklart) der en rigg var
    navngitt — det viste seg i praksis å dra inn tiår gammel administrativ saldo Sodir aldri har
    ryddet opp i (eks-operatørnavn som «Statoil ASA (old)», og rigger som for lengst er skrotet),
    IKKE reelt kommende aktivitet. Reversert — se riggbekreftelse.py for en bedre løsning: en rigg
    utenfor «planlagt»-lista tas bare med når det finnes en FERSK, ekte Miljødirektoratet-høring
    som bekrefter den, ikke bare fordi et gammelt feltnavn tilfeldigvis står utfylt."""
    kolonner = ["Rigg", "Brønn(er)", "Antall", "Operatør", "Nærmeste dato", "Dager til", "Merknad"]
    p = df[df["kategori"] == "planlagt"].copy()
    if p.empty:
        return pd.DataFrame(columns=kolonner)
    p["_dato"] = p["planlagt_start"].fillna(p["tillatelse"])
    p["rigg"] = p["rigg"].fillna("Rigg ikke tildelt")
    rader = []
    for rigg, g in p.groupby("rigg"):
        dato = g["_dato"].min()
        dager_til = (dato - pd.Timestamp.now().normalize()).days if pd.notna(dato) else None
        rader.append({
            "Rigg": rigg,
            "Brønn(er)": ", ".join(g.sort_values("_dato")["navn"].dropna()),
            "Antall": len(g),
            "Operatør": ", ".join(sorted(set(g["operator"].dropna()))),
            "Nærmeste dato": dato,
            "Dager til": dager_til,
            "Merknad": _forsinket_merke(dager_til),
        })
    tab = pd.DataFrame(rader, columns=kolonner).sort_values("Nærmeste dato", na_position="last")
    tab["Nærmeste dato"] = tab["Nærmeste dato"].dt.strftime("%d.%m.%Y").fillna("Ukjent")
    tab["Dager til"] = tab["Dager til"].astype("Int64")
    return tab.reset_index(drop=True)


def annet_tabell(df: pd.DataFrame, kategori: str = "annet") -> pd.DataFrame:
    """Brønner i valgt kategori ("annet" = tidlig fase/uavklart, "avlyst" = eksplisitt «vil aldri
    bli boret»). Rå status vises slik at du selv kan dobbeltsjekke klassifiseringen."""
    a = df[df["kategori"] == kategori]
    return pd.DataFrame({
        "Brønn": a["navn"],
        "Status (rått fra Sodir)": a["status"].fillna("(tom)"),
        "Operatør": a["operator"],
        "Rigg": a["rigg"],
        "Formål": a["formaal_visning"],
        "Felt": a["felt"].fillna(a["funn"]),
        "Område": a["omraade"],
        "Lisens": a["lisens"],
    }).sort_values(["Status (rått fra Sodir)", "Brønn"]).reset_index(drop=True)


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
    """Lesbar tabell over alle synlige brønner: borende først, så planlagte, så «annet», så ferdige (nyeste først)."""
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


def forenkle_kolonner(tabell: pd.DataFrame, behold: list[str]) -> pd.DataFrame:
    """Trimmer en allerede ferdigbygd visningstabell (en av tabellene over) ned til et utvalg
    kolonner — brukt av «📱 Kompakt modus» i app.py, for en kortere, mer mobilvennlig tabell UTEN
    å måtte bygge egne, parallelle tabell-funksjoner for hver fane. Kolonner i `behold` som ikke
    finnes i `tabell` (f.eks. en kolonne en gitt fane uansett ikke har) hoppes stille over, i
    stedet for å kaste KeyError — samme «vær tolerant for det som mangler»-prinsipp som resten av
    appen. Beholder rekkefølgen fra `behold`, ikke fra den opprinnelige tabellen. ALDRI brukt til
    selve CSV-eksporten andre steder i appen — den skal alltid inneholde ALLE kolonner, uansett
    hva som vises på skjermen."""
    kolonner = [k for k in behold if k in tabell.columns]
    return tabell[kolonner] if kolonner else tabell