"""Bygger et «ukentlig sammendrag»: en ferdigformatert e-posttekst som slår sammen nye høringer
(siden forrige sammendrag) og det som skjer de neste ukene (brønnoppstart + høringsfrister), hentet
fra de samme dataene resten av appen allerede har — ingen egne nettverkskall her.

All bygging av selve TEKSTEN er rene funksjoner (ingen Streamlit, ingen e-post-sending) slik at det
kan testes for seg selv — se epost.py for selve SMTP-sendingen, og
send_ukentlig_sammendrag_cli.py for det frittstående skriptet som kan kjøres av en
systemplanlegger (cron/Windows Task Scheduler) for faktisk automatisk, ukentlig sending; appen her
kan bare generere/sende «nå», siden en Streamlit-side ikke kan vekke seg selv en gang i uken uten
at noen har den åpen.

Tilstanden («hvilke høring-URL-er har vi varslet om før») lagres lokalt i en JSON-fil, samme
mønster som favoritter.py/legitimasjon.py, slik at «nye høringer siden sist» også virker riktig på
tvers av økter/omstarter — ikke bare innenfor én nettleserøkt (det ville vært for kort til noe som
skal være UKENTLIG)."""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

import tidslinje as tid

FIL = Path(__file__).parent / "sammendrag_tilstand.json"


def last_tilstand() -> dict:
    """Leser lagret tilstand fra disk: hvilke høring-URL-er forrige sammendrag inneholdt, og når
    det sist ble generert. Tom tilstand (aldri generert før) gir en tom URL-mengde, slik at
    FØRSTE sammendrag viser ALLE funnede høringer som «nye» (det finnes ingen «sist» å sammenligne
    mot ennå) i stedet for å late som ingenting er nytt."""
    if not FIL.exists():
        return {"sist_generert": None, "sett_urls": set()}
    try:
        data = json.loads(FIL.read_text(encoding="utf-8"))
        return {"sist_generert": data.get("sist_generert"), "sett_urls": set(data.get("sett_urls", []))}
    except (json.JSONDecodeError, OSError):
        return {"sist_generert": None, "sett_urls": set()}


def lagre_tilstand(sett_urls: set[str], tidspunkt: str) -> bool:
    """Lagrer tilstanden på disk etter at et sammendrag er generert/sendt. Returnerer False (uten
    å krasje) hvis mappen er skrivebeskyttet."""
    try:
        FIL.write_text(
            json.dumps({"sist_generert": tidspunkt, "sett_urls": sorted(sett_urls)}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return True
    except OSError:
        return False


def bygg_sammendrag(
    bronner: pd.DataFrame, horinger: list[dict], favoritter: set[str],
    forrige_sett_urls: set[str], dager_fremover: int = 14, i_dag: date | None = None,
    frekvens: str = "ukentlig", inkluder_bronner: bool = True, inkluder_horinger: bool = True,
    bare_favoritter: bool = False,
) -> dict:
    """Bygger selve sammendraget. Ren funksjon — tar allerede hentede data inn (samme `bronner`,
    `horinger` og `favoritter` som resten av appen bruker), ingen nettverkskall eller
    diskaksess her.

    `frekvens` er bare brukt i emnet/overskriften («ukentlig»/«daglig») — selve sendefrekvensen
    styres av HVOR OFTE send_ukentlig_sammendrag_cli.py faktisk blir kjørt (av cron/Oppgave-
    planlegger), ikke av denne funksjonen; se «🚀 Kom i gang»-popupen (oppsett_popup.py) som setter
    `dager_fremover` kortere for «daglig» enn for «ukentlig».
    `inkluder_bronner`/`inkluder_horinger`: slår av en av de to seksjonene helt (f.eks. hvis du bare
    følger høringer, ikke brønnoppstart, eller omvendt).
    `bare_favoritter`: når True, begrenses BÅDE «nye høringer» og «kommende»-seksjonen til rader der
    en gjenkjent rigg er i `favoritter` — rader uten noen gjenkjent rigg utelates da også, siden det
    ikke finnes noe favoritt-signal å filtrere på for dem.

    Returnerer {"emne": str, "tekst": str, "denne_gang_urls": set[str]} — sistnevnte er ment
    lagret (via lagre_tilstand()) etter at sammendraget er vist/sendt, slik at NESTE sammendrag vet
    hva som allerede er varslet om."""
    i_dag = i_dag or date.today()
    slutt = i_dag + timedelta(days=dager_fremover)

    def _rigg_favoritt(rigg: str | None) -> bool:
        return bool(rigg) and rigg in favoritter

    horinger_for_bygging = horinger if inkluder_horinger else []
    bronner_for_bygging = bronner if inkluder_bronner else bronner.iloc[0:0]

    nye_horinger = [h for h in horinger_for_bygging if h.get("url") not in forrige_sett_urls]
    if bare_favoritter:
        nye_horinger = [h for h in nye_horinger if _rigg_favoritt(h.get("rigg"))]
    # «denne_gang_urls» spores uansett blant ALLE høringer (ikke bare favoritt-filtrerte) — ellers
    # ville en høring uten favorittrigg bli vist som «ny» på nytt i hvert fremtidig sammendrag.
    denne_gang_urls = {h["url"] for h in horinger if h.get("url")}

    kommende = [r for r in tid.bygg_tidslinje(bronner_for_bygging, horinger_for_bygging) if i_dag <= r["dato"] <= slutt]
    if bare_favoritter:
        kommende = [r for r in kommende if _rigg_favoritt(r["detaljer"].get("Rigg"))]

    linjer = [
        f"SOKKELRADAR — {frekvens} sammendrag ({i_dag.strftime('%d.%m.%Y')})",
        "=" * 50, "",
    ]

    if inkluder_horinger:
        linjer.append(f"🆕 NYE HØRINGER SIDEN SIST ({len(nye_horinger)})")
        linjer.append("-" * 50)
        if not nye_horinger:
            linjer.append("Ingen nye høringer siden forrige sammendrag.")
        else:
            for h in nye_horinger:
                rigg_merke = " ⭐" if _rigg_favoritt(h.get("rigg")) else ""
                detaljer = " · ".join(
                    f"{navn}: {h.get(felt)}" for navn, felt in (("Felt", "felt"), ("Rigg", "rigg"), ("Operatør", "operator"))
                    if h.get(felt)
                )
                linjer.append(f"- {h['tittel']}{rigg_merke}")
                if detaljer:
                    linjer.append(f"  {detaljer}")
                linjer.append(f"  {h['url']}")
        linjer.append("")

    linjer.append(f"📅 KOMMENDE {dager_fremover} DAGER (brønnoppstart og høringer)")
    linjer.append("-" * 50)
    if not kommende:
        linjer.append(f"Ingenting datert i løpet av de neste {dager_fremover} dagene med disse dataene.")
    else:
        for etikett, rader in tid.grupper_etter_maned(kommende):
            linjer.append(f"[{etikett}]")
            for r in rader:
                rigg_navn = r["detaljer"].get("Rigg")
                rigg_merke = " ⭐" if _rigg_favoritt(rigg_navn) else ""
                linjer.append(f"  {r['dato'].strftime('%d.%m.%Y')} {r['ikon']} {r['tittel']}{rigg_merke}")
    linjer.append("")
    linjer.append(
        "(Generert av Sokkelradar. Datoer er Sodir/Miljødirektoratets egne estimater og kan endre "
        "seg — sjekk appen for detaljer og de ferskeste tallene.)"
    )

    emne = f"Sokkelradar – {frekvens} sammendrag ({i_dag.strftime('%d.%m.%Y')})"
    return {"emne": emne, "tekst": "\n".join(linjer), "denne_gang_urls": denne_gang_urls}