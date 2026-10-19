"""Frittstående skript for FAKTISK automatisk, ukentlig e-postsending — uten at Sokkelradar-appen
(Streamlit) trenger å stå åpen i en nettleser. Streamlit kan bare kjøre kode når noen har siden
oppe, så den delen av «Ukentlig sammendrag»-expanderen i app.py («Generer/Send nå») virker kun der
og da; DETTE skriptet er i stedet ment kjørt av din egen maskins systemplanlegger, f.eks.:

    Windows (Oppgaveplanlegger / Task Scheduler):
        Opprett en ny oppgave som kjører ukentlig ELLER daglig — se «Frekvens»-valget du satte i
        «🚀 Kom i gang»-popupen eller «📧 Ukentlig sammendrag»-expanderen; DENNE delen (hvor ofte
        systemplanleggeren faktisk starter skriptet) må settes opp der, appen kan ikke styre det:
            Program/skript: python
            Argumenter:      send_ukentlig_sammendrag_cli.py
            Start i:         (mappen der denne filen og de andre sokkelradar-filene ligger)

    Linux/macOS (cron), f.eks. hver mandag kl. 07:00 (ukentlig) eller hver dag kl. 07:00 (daglig):
        0 7 * * 1  cd /sti/til/sokkelradar && python3 send_ukentlig_sammendrag_cli.py >> sammendrag_logg.txt 2>&1
        0 7 * * *  cd /sti/til/sokkelradar && python3 send_ukentlig_sammendrag_cli.py >> sammendrag_logg.txt 2>&1

Forutsetter at SMTP-innstillingene allerede er lagret (via «🚀 Kom i gang»-popupen eller «💾 Lagre
SMTP-innstillinger» i app.py, begge skriver til epost_innstillinger.json) — se epost.py sin
advarsel om at dette lagres i klartekst. Bruker hearing-tidsgrensen 90 dager og alle felt (samme
standardverdier som appens «bredt søk» — altså ingen innsnevring til et bestemt ansvarsområde),
siden skriptet kjører uten noen bruker til stede som kan velge et snevrere «Ansvarsområde» akkurat
denne gangen. Hvor mange dager FREMOVER selve sammendraget ser på (og om det inkluderer
brønnoppstart/høringer/bare favoritter) styres derimot av de lagrede valgene, se sammendrag.py.

Avslutter med kode 0 ved vellykket sending (eller hvis SMTP ikke er konfigurert ennå — se utskrift
til stdout/logg), og 1 hvis selve e-postsendingen feilet, slik at en systemplanlegger kan varsle
deg om feilen (f.eks. Windows Oppgaveplanlegger sin «siste kjøring mislyktes»-status)."""
from __future__ import annotations

import sys
from datetime import date, datetime

import databehandling as db
import epost
import favoritter
import hent_data
import miljodirektoratet as md
import oppsett_popup as op
import sammendrag


def main() -> int:
    print(f"[{datetime.now().isoformat(timespec='seconds')}] Starter ukentlig sammendrag …")

    innstillinger = epost.last()
    if not innstillinger["smtp_server"]:
        print(
            "Ingen SMTP-innstillinger lagret ennå. Åpne appen (streamlit run app.py), fyll inn "
            "«📧 Ukentlig sammendrag»-expanderen og trykk «💾 Lagre SMTP-innstillinger» først."
        )
        return 0

    print("Henter brønner fra Sodir …")
    features = hent_data.hent_bronn_features(date.today().year - 2)
    bronner, _ = db.tolk_bronner(features, hent_data.BRONN_FELT)
    felt_liste = sorted(set(bronner["felt"].dropna()) | set(bronner["funn"].dropna()))

    print("Henter høringer fra Miljødirektoratet (kan ta litt tid, åpner en hodeløs nettleser) …")
    try:
        resultater, feilet, _, diag = md.hent_relevante_horinger(felt_liste, maks=200, dager_tilbake=90)
    except Exception as e:  # samme «ikke la én feilende kilde stoppe resten»-filosofi som appen
        print(f"Fikk ikke hentet høringer ({e}) — fortsetter med bare brønndata.")
        resultater = []
    else:
        if feilet:
            print(f"({len(feilet)} høring(er) kunne ikke tolkes og ble hoppet over — se appen for detaljer.)")

    favoritter_nå = favoritter.last()
    tilstand = sammendrag.last_tilstand()
    frekvens = innstillinger.get("frekvens", "ukentlig")
    resultat = sammendrag.bygg_sammendrag(
        bronner, resultater, favoritter_nå, tilstand["sett_urls"],
        dager_fremover=op.DAGER_FREMOVER_FOR_FREKVENS.get(frekvens, 14), frekvens=frekvens,
        inkluder_bronner=innstillinger.get("inkluder_bronner", True),
        inkluder_horinger=innstillinger.get("inkluder_horinger", True),
        bare_favoritter=innstillinger.get("bare_favoritter", False),
    )

    print(f"Sender «{resultat['emne']}» til {innstillinger['mottaker']} …")
    try:
        epost.send_epost(resultat["emne"], resultat["tekst"], innstillinger)
    except epost.EpostFeil as e:
        print(f"FEIL ved sending: {e}")
        return 1

    sammendrag.lagre_tilstand(resultat["denne_gang_urls"], datetime.now().isoformat())
    print("Sendt OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())