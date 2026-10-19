"""Tidslinje-fanen: slår sammen det TO forskjellige kildene appen allerede har om «hva skjer
fremover» — planlagte/tillatte brønner fra Sodir, og tidlig-varsel-høringer fra Miljødirektoratet
(se fane_horinger.py) — i én kronologisk liste, gruppert per måned. Poenget er oversikt: i stedet
for å sjekke to forskjellige faner for å se hva som kommer, én liste sortert på dato.

Leser høringene fra st.session_state["horing_siste_resultater"] (satt av fane_horinger.vis_fane()
etter et søk der) i stedet for å hente dem på nytt — dette er bevisst, både for å unngå et unødvendig
API-kall og fordi "tidslinjen" skal vise akkurat det du sist så i Høringer-fanen, ikke et skjult,
annerledes søk i bakgrunnen.

All ren logikk (dato-tolking, bygging og gruppering av rader) er skilt fra Streamlit-kallene i
vis_fane() nederst, slik at den kan testes uten noen av Streamlit/pandas-avhengighetene satt opp."""
from __future__ import annotations

import re
from datetime import date, timedelta

import pandas as pd
import streamlit as st

_MANEDER_NO = {
    "januar": 1, "februar": 2, "mars": 3, "april": 4, "mai": 5, "juni": 6, "juli": 7,
    "august": 8, "september": 9, "oktober": 10, "november": 11, "desember": 12,
}
_MANEDSNAVN_NO = {v: k.capitalize() for k, v in _MANEDER_NO.items()}

_DATO_KORT = re.compile(r"^\s*(\d{1,2})\.(\d{1,2})\.(\d{4})\s*$")
_DATO_LANG = re.compile(
    r"^\s*(\d{1,2})\.\s*(" + "|".join(_MANEDER_NO) + r")\s*(\d{4})\s*$", re.I,
)


def tolk_dato_tekst(tekst: str | None) -> date | None:
    """Tolker datoformatene miljodir_tolk.py trekker ut med regex: enten «1.11.2026» eller
    «1. november 2026». Returnerer None (ALDRI en gjetning) hvis teksten ikke matcher noen av de
    to formatene, eller selve tallene gir en umulig dato (f.eks. 31. februar)."""
    if not tekst:
        return None
    m = _DATO_KORT.match(tekst)
    if m:
        dag, mnd, ar = (int(x) for x in m.groups())
        try:
            return date(ar, mnd, dag)
        except ValueError:
            return None
    m = _DATO_LANG.match(tekst)
    if m:
        dag = int(m.group(1))
        mnd = _MANEDER_NO[m.group(2).lower()]
        ar = int(m.group(3))
        try:
            return date(ar, mnd, dag)
        except ValueError:
            return None
    return None


def _brønn_rader(bronner: pd.DataFrame) -> list[dict]:
    """Én rad per brønn som har EN av de to datoene Sodir oppgir for noe som ikke har startet ennå
    (planlagt_start eller, i mangel av det, tillatelse-dato) — brønner UTEN noen av delene (typisk
    «Annet / uavklart») har ingenting å plassere på en tidslinje, og utelates derfor her (de har sin
    egen fane allerede, «Annet / uavklart», nettopp fordi de mangler dato)."""
    if bronner.empty:
        return []
    rader = []
    for _, r in bronner.iterrows():
        dato_ts = r["planlagt_start"] if pd.notna(r["planlagt_start"]) else r["tillatelse"]
        if pd.isna(dato_ts):
            continue
        rader.append({
            "dato": dato_ts.date(),
            "type": "brønn",
            "ikon": "🛢",
            "tittel": r["navn"],
            "detaljer": {
                "Operatør": r.get("operator"), "Rigg": r.get("rigg"), "Felt/funn": r.get("felt") or r.get("funn"),
                "Område": r.get("omraade"), "Status": r.get("status"),
            },
            "forklaring": (
                "Planlagt oppstart (Sodir)." if pd.notna(r["planlagt_start"])
                else "Boretillatelse gitt (Sodir) — planlagt oppstart ikke satt ennå."
            ),
        })
    return rader


def _høring_rader(horinger: list[dict]) -> list[dict]:
    """Én rad per høring som har EN tolkbar dato: «tidligst forventet oppstart» foretrekkes (det er
    selve boringen tidslinjen skal vise), og i mangel av det høringsfristen — hvis IKKE den allerede
    er utløpt, siden en utløpt frist ikke lenger er noe å planlegge rundt. Høringer uten noen av
    delene (f.eks. feltet ikke gjenkjent og ingen dato funnet i teksten) utelates her — de vises
    fortsatt i selve Høringer-fanen, der «sammendrag» uansett er tilgjengelig som sikkerhetsnett."""
    rader = []
    for r in horinger:
        dato = tolk_dato_tekst(r.get("tidligst_oppstart"))
        if dato is not None:
            forklaring = "Tidligst forventet oppstart (høring hos Miljødirektoratet)."
            ikon = "🔭"
        elif r.get("frist") and not r.get("frist_utlopt"):
            dato = tolk_dato_tekst(r["frist"])
            forklaring = "Høringsfrist (ingen oppgitt oppstartsdato i teksten ennå)."
            ikon = "📝"
        else:
            continue
        if dato is None:
            continue
        rader.append({
            "dato": dato, "type": "høring", "ikon": ikon, "tittel": r["tittel"], "url": r["url"],
            "detaljer": {
                "Felt": r.get("felt"), "Brønn": r.get("bronn"), "Operatør": r.get("operator"),
                "Rigg": r.get("rigg"),
            },
            "forklaring": forklaring,
        })
    return rader


def bygg_tidslinje(bronner: pd.DataFrame, horinger: list[dict]) -> list[dict]:
    """Slår sammen brønn- og høringsrader til én liste, sortert kronologisk (eldste/nærmeste
    først). Rene data, ingen Streamlit — se vis_fane() for selve visningen."""
    rader = _brønn_rader(bronner) + _høring_rader(horinger)
    rader.sort(key=lambda r: r["dato"])
    return rader


def grupper_etter_maned(rader: list[dict]) -> list[tuple[str, list[dict]]]:
    """Grupperer en allerede sortert rad-liste i (månedsetikett, rader)-par, i samme rekkefølge
    radene kom i (så gruppene selv havner kronologisk)."""
    grupper: list[tuple[str, list[dict]]] = []
    for r in rader:
        etikett = f"{_MANEDSNAVN_NO[r['dato'].month]} {r['dato'].year}"
        if grupper and grupper[-1][0] == etikett:
            grupper[-1][1].append(r)
        else:
            grupper.append((etikett, [r]))
    return grupper


def vis_fane(bronner: pd.DataFrame) -> None:
    """Tegner «🗓️ Tidslinje»-fanen: kombinert, kronologisk oversikt over kommende brønner (Sodir)
    og høringer (Miljødirektoratet, tidlig varsel)."""
    horinger = st.session_state.get("horing_siste_resultater")
    if horinger is None:
        st.caption(
            "Viser planlagte/tillatte brønner fra Sodir. Høringer fra Miljødirektoratet vises også "
            "her så snart du har trykket «Søk i høringer nå» i «🔭 Høringer (tidlig varsel)»-fanen "
            "minst én gang i denne økten."
        )
        horinger = []
    else:
        st.caption(
            "Kombinert, kronologisk oversikt: planlagte/tillatte brønner fra Sodir og høringer fra "
            "Miljødirektoratet (tidlig varsel) — sistnevnte er akkurat det du sist søkte opp i "
            "«🔭 Høringer (tidlig varsel)»-fanen, ikke et nytt søk i bakgrunnen."
        )

    skjul_passert = st.checkbox("Skjul det som allerede er passert", value=True, key="tidslinje_skjul_passert")
    rader = bygg_tidslinje(bronner, horinger)
    if skjul_passert:
        i_dag = date.today()
        rader = [r for r in rader if r["dato"] >= i_dag - timedelta(days=1)]

    if not rader:
        st.info("Ingen datoer å vise ennå med disse innstillingene.")
        return

    i_dag = date.today()
    for etikett, gruppe_rader in grupper_etter_maned(rader):
        st.markdown(f"#### {etikett}")
        for r in gruppe_rader:
            forsinket = r["dato"] < i_dag
            dato_tekst = r["dato"].strftime("%d.%m.%Y") + (" (passert)" if forsinket else "")
            with st.container(border=True):
                if r["type"] == "høring":
                    st.markdown(f"{r['ikon']} **{dato_tekst}** — [{r['tittel']}]({r['url']})")
                else:
                    st.markdown(f"{r['ikon']} **{dato_tekst}** — {r['tittel']}")
                st.caption(r["forklaring"])
                detaljer = [f"{k}: **{v}**" for k, v in r["detaljer"].items() if v]
                if detaljer:
                    st.caption(" · ".join(detaljer))