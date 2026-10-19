"""Kobler «Kommende rigger»-tabellen (databehandling.rigger_planlagt()) mot ferske
Miljødirektoratet-høringer, som et ekstra bekreftelsessignal.

Bakgrunn: Sodir sitt «planlagt»-felt kan bli liggende lenge etter at den egentlige datoen er
passert — eller aldri fått noen dato i det hele tatt (se databehandling.GRENSE_FORSINKET_DAGER
og rigger_planlagt() sin docstring om «annet»-brønner med navngitt rigg) — fordi Sodir ikke
oppdaterer status automatisk. En FERSK Miljødirektoratet-høring som nevner samme rigg er et reelt
tegn på at prosjektet fortsatt er aktivt, uavhengig av hvor gammel (eller manglende) Sodir sin
egen dato er.

Henting av selve høringene skjer et annet sted (fane_horinger.py sin «Høringer»-fane, lagret i
st.session_state.horing_siste_resultater etter et søk der) — denne fila gjør bare selve koblingen,
og er derfor testbar helt uten nettverk/nettleser."""
from __future__ import annotations

from datetime import date

import pandas as pd

from fane_horinger import _match_kjent_rigg
from miljodir_skrap import _tolk_publisert_dato


def siste_bekreftelse_per_rigg(horinger: list[dict], alle_rigger: list[str]) -> dict[str, str]:
    """{riggnavn (normalisert mot Sodir): dato-tekst (dd.mm.åååå) for den FERSKESTE høringen som
    nevner riggen}. Høringer uten gjenkjent rigg, uten en tolkbar publiseringsdato, eller der
    riggnavnet ikke matcher noe KJENT Sodir-navn (se _match_kjent_rigg() sin «bekreftet»-flagg)
    telles ikke med — en usikker gjetning skal ikke se ut som en bekreftelse."""
    nyest: dict[str, date] = {}
    for h in horinger:
        rigg_tekst = h.get("rigg")
        if not rigg_tekst:
            continue
        dato = _tolk_publisert_dato(h.get("publisert"))
        if dato is None:
            continue
        navn, bekreftet = _match_kjent_rigg(rigg_tekst, alle_rigger)
        if not bekreftet:
            continue
        if navn not in nyest or dato > nyest[navn]:
            nyest[navn] = dato
    return {navn: dato.strftime("%d.%m.%Y") for navn, dato in nyest.items()}


def beriket_med_bekreftelse(tabell: pd.DataFrame, bekreftelser: dict[str, str]) -> pd.DataFrame:
    """Kopi av `tabell` (fra databehandling.rigger_planlagt()) der «Merknad» oppdateres med et
    ✅-signal for rigger som har en fersk høring — men BARE for rader der dette faktisk sier noe
    nytt: en rigg med en dato i fremtiden er ikke i tvil og trenger ingen bekreftelse. Erstatter
    (ikke supplerer) et eventuelt eksisterende ⚠️-merke, fordi en fersk høring er et sterkere
    signal enn mangelen på en oppdatert Sodir-status."""
    if tabell.empty or not bekreftelser:
        return tabell
    t = tabell.copy()
    for i, rad in t.iterrows():
        dato_str = bekreftelser.get(rad["Rigg"])
        if not dato_str:
            continue
        dager_til = rad["Dager til"]
        if pd.notna(dager_til) and dager_til >= 0:
            continue  # dato i fremtiden — ikke i tvil, ingen grunn til å «bekrefte»
        t.at[i, "Merknad"] = f"✅ Bekreftet aktiv (høring publisert {dato_str})"
    return t


def nye_rader_fra_horinger(tabell: pd.DataFrame, bekreftelser: dict[str, str]) -> pd.DataFrame:
    """Ekstra rader for rigger som en fersk, gjenkjent høring nevner, men som IKKE finnes i
    `tabell` (fra databehandling.rigger_planlagt()) i det hele tatt — altså en rigg Sodir ennå
    ikke har registrert noen «planlagt»-brønn for. Dette var tidligere forsøkt løst ved å dra inn
    HELE «annet»-kategorien (se databehandling.rigger_planlagt() sin docstring for hvorfor det ble
    reversert — mest tiår gammel administrativ saldo, ikke reell aktivitet); en ekte, fersk høring
    er derimot et konkret signal, så disse radene er trygge å vise. Ingen brønnnavn/operatør å
    vise (bare riggnavnet er kjent fra høringen) — det sies eksplisitt, i stedet for å late som vi
    vet mer enn vi gjør."""
    kolonner = ["Rigg", "Brønn(er)", "Antall", "Operatør", "Nærmeste dato", "Dager til", "Merknad"]
    if not bekreftelser:
        return pd.DataFrame(columns=kolonner)
    kjente_rigger = set(tabell["Rigg"]) if not tabell.empty else set()
    nye = sorted(navn for navn in bekreftelser if navn not in kjente_rigger)
    if not nye:
        return pd.DataFrame(columns=kolonner)
    return pd.DataFrame([{
        "Rigg": navn,
        "Brønn(er)": "(ingen brønn registrert som «planlagt» av Sodir ennå)",
        "Antall": 0,
        "Operatør": "",
        "Nærmeste dato": "Ukjent",
        "Dager til": pd.NA,
        "Merknad": f"✅ Bekreftet aktiv (høring publisert {bekreftelser[navn]}) — kun kjent fra Miljødirektoratet foreløpig",
    } for navn in nye], columns=kolonner)