"""Logikken bak «🚀 Kom i gang»-popupen i app.py: en dialog som dukker opp automatisk (ikke noe du
må bla for å finne) rett etter at dashbordet har lastet, og samler BarentsWatch-nøkkelen og
e-post/sammendrag-oppsettet på ett sted.

Kan IKKE automatisere selve kontoopprettelsen (BarentsWatch-konto, Gmail/Outlook-app-passord) —
det krever at DU aksepterer vilkår/bruker BankID/logger inn et ekte sted, noe et program aldri bør
gjøre på dine vegne. Det popupen derimot kan gjøre: gjette SMTP-server fra e-postadressen din (så
du bare fyller ut 2 felt der i stedet for 4), og vise seg selv uten at du må finne riktig expander.

All beslutningslogikk (når skal popupen vises, hvilken SMTP-server en e-post sannsynligvis bruker)
er rene funksjoner her, uten Streamlit — selve dialogen (st.dialog) ligger i app.py, siden den må
lese/skrive widgets og st.session_state direkte."""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

FIL = Path(__file__).parent / "oppsett_popup_tilstand.json"

# Bare leverandører jeg er sikker på SMTP-adressen til — ved tvil vises i stedet et fritekstfelt
# der du skriver inn serveren selv, i stedet for å gjette feil og få en kryptisk tilkoblingsfeil.
DOMENE_SMTP: dict[str, tuple[str, int]] = {
    "gmail.com": ("smtp.gmail.com", 587),
    "googlemail.com": ("smtp.gmail.com", 587),
    "outlook.com": ("smtp.office365.com", 587),
    "hotmail.com": ("smtp.office365.com", 587),
    "live.com": ("smtp.office365.com", 587),
    "msn.com": ("smtp.office365.com", 587),
    "yahoo.com": ("smtp.mail.yahoo.com", 587),
    "yahoo.no": ("smtp.mail.yahoo.com", 587),
    "icloud.com": ("smtp.mail.me.com", 587),
    "me.com": ("smtp.mail.me.com", 587),
    "mac.com": ("smtp.mail.me.com", 587),
}

# Hvor mange dager fremover sammendraget skal se på, avhengig av valgt frekvens — et «ukentlig»
# sammendrag med bare 3 dagers utsikt ville gått glipp av det meste av uken; et «daglig» med 14
# dagers utsikt ville vist samme lange liste hver dag og druknet det som faktisk er nytt.
DAGER_FREMOVER_FOR_FREKVENS = {"ukentlig": 14, "daglig": 3}


def gjett_smtp_server(epost: str) -> tuple[str, int] | None:
    """Gjetter SMTP-server/port fra e-postadressens domene — KUN for domener i DOMENE_SMTP over
    (ALDRI en gjetning utover den lista; ukjent domene gir None, så appen heller viser et
    fritekstfelt enn å sende et garantert feil tilkoblingsforsøk til en feil server)."""
    if not epost or "@" not in epost:
        return None
    domene = epost.rsplit("@", 1)[-1].strip().lower()
    return DOMENE_SMTP.get(domene)


def last_tilstand() -> dict:
    """Leser lagret popup-tilstand fra disk: om du har bedt om å ikke se den igjen, og når den
    sist ble vist (for å vente en stund før den eventuelt spør igjen etter «Ikke nå»)."""
    if not FIL.exists():
        return {"spor_ikke_igjen": False, "sist_vist": None}
    try:
        data = json.loads(FIL.read_text(encoding="utf-8"))
        return {"spor_ikke_igjen": bool(data.get("spor_ikke_igjen", False)), "sist_vist": data.get("sist_vist")}
    except (json.JSONDecodeError, OSError):
        return {"spor_ikke_igjen": False, "sist_vist": None}


def lagre_tilstand(spor_ikke_igjen: bool, sist_vist: str | None) -> bool:
    """Lagrer popup-tilstanden på disk. Returnerer False (uten å krasje) hvis mappen er
    skrivebeskyttet — da spør popupen bare på nytt neste økt i stedet."""
    try:
        FIL.write_text(
            json.dumps({"spor_ikke_igjen": spor_ikke_igjen, "sist_vist": sist_vist}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return True
    except OSError:
        return False


def skal_vise_popup(
    tilstand: dict, bw_konfigurert: bool, epost_konfigurert: bool, i_dag: date | None = None,
    ventedager: int = 7,
) -> bool:
    """Ren beslutningsfunksjon — tar allerede lest tilstand/konfigurasjonsstatus inn, ingen
    diskaksess her (se last_tilstand()).

    Regler:
    - Bedt om å aldri se den igjen -> alltid False.
    - Begge deler (BarentsWatch OG e-post) allerede satt opp -> ingenting å spørre om, False.
    - Aldri vist før («sist_vist» er None) -> True.
    - Vist før, men for `ventedager` dager siden eller mer (du trykket «Ikke nå» den gangen, og
      minst én av de to delene står fortsatt uferdig) -> True igjen.
    - Ellers (vist nylig) -> False, ikke nag for hver gang appen åpnes."""
    if tilstand.get("spor_ikke_igjen"):
        return False
    if bw_konfigurert and epost_konfigurert:
        return False
    sist_vist = tilstand.get("sist_vist")
    if not sist_vist:
        return True
    i_dag = i_dag or date.today()
    try:
        sist_vist_dato = date.fromisoformat(str(sist_vist)[:10])
    except ValueError:
        return True  # ikke en gjenkjennelig dato -> tolk som «ikke vist før», ikke en krasj
    return i_dag - sist_vist_dato >= timedelta(days=ventedager)