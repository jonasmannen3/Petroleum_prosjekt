"""E-postsending (SMTP) og valgfri lagring av SMTP-innstillinger i en lokal JSON-fil — samme
mønster som legitimasjon.py (BarentsWatch-nøkkelen). Brukes av «Ukentlig sammendrag»-seksjonen i
app.py, og av det frittstående skriptet send_ukentlig_sammendrag_cli.py (ment kjørt av en
systemplanlegger, se docstringen der, siden Streamlit-appen i seg selv ikke kan «våkne av seg
selv» en gang i uken uten at noen har den åpen).

OBS: innstillingene (inkludert passordet) lagres i KLARTEKST i denne filen, på din egen maskin —
samme avveining som er gjort for BarentsWatch-nøkkelen allerede. Bruk helst et eget
app-passord/engangspassord for e-postkontoen (f.eks. Gmail sitt «app password») i stedet for
hovedpassordet ditt, slik at du kan trekke tilbake akkurat denne tilgangen senere uten å bytte
hele kontopassordet."""
from __future__ import annotations

import json
import smtplib
from email.mime.text import MIMEText
from pathlib import Path

FIL = Path(__file__).parent / "epost_innstillinger.json"

TOMME_INNSTILLINGER = {
    "smtp_server": "", "smtp_port": 587, "bruker": "", "passord": "", "mottaker": "",
    # Lagt til for «🚀 Kom i gang»-popupen (se oppsett_popup.py): hvor ofte sammendraget skal
    # dekke («ukentlig»/«daglig», styrer hvor mange dager fremover send_ukentlig_sammendrag_cli.py
    # ser på), og hva det skal inneholde — uavhengig av selve SMTP-tilkoblingen over.
    "frekvens": "ukentlig", "inkluder_bronner": True, "inkluder_horinger": True, "bare_favoritter": False,
}


class EpostFeil(Exception):
    """Alt som kan gå galt med selve sendingen (feil server, feil passord, nettverk, mottaker
    avvist osv.) samles i denne ene feiltypen — meldingen fra smtplib/socket brukes direkte,
    siden den vanligvis sier akkurat hva som er galt (f.eks. «Authentication failed»)."""


def last() -> dict:
    """Leser lagrede SMTP-innstillinger fra disk. Returnerer tomme verdier hvis ingenting er
    lagret ennå (ALDRI en gjetning på en server/bruker)."""
    if not FIL.exists():
        return dict(TOMME_INNSTILLINGER)
    try:
        data = json.loads(FIL.read_text(encoding="utf-8"))
        return {
            "smtp_server": str(data.get("smtp_server", "")),
            "smtp_port": int(data.get("smtp_port", 587) or 587),
            "bruker": str(data.get("bruker", "")),
            "passord": str(data.get("passord", "")),
            "mottaker": str(data.get("mottaker", "")),
            # .get() med fallback: eldre lagrede filer (fra før popupen) har ikke disse feltene —
            # IKKE en gjetning på brukerens faktiske preferanse, bare de samme standardverdiene
            # som gjaldt implisitt før valgene fantes.
            "frekvens": str(data.get("frekvens", "ukentlig")),
            "inkluder_bronner": bool(data.get("inkluder_bronner", True)),
            "inkluder_horinger": bool(data.get("inkluder_horinger", True)),
            "bare_favoritter": bool(data.get("bare_favoritter", False)),
        }
    except (json.JSONDecodeError, OSError, ValueError, TypeError):
        return dict(TOMME_INNSTILLINGER)


def lagre(innstillinger: dict) -> bool:
    """Lagrer innstillingene på disk. Returnerer False (uten å krasje) hvis mappen er
    skrivebeskyttet — da må de skrives inn på nytt neste økt i stedet."""
    try:
        FIL.write_text(json.dumps(innstillinger, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except OSError:
        return False


def slett() -> None:
    try:
        FIL.unlink(missing_ok=True)
    except OSError:
        pass


def send_epost(emne: str, tekst: str, innstillinger: dict) -> None:
    """Sender ren tekst-e-post via SMTP (STARTTLS på port 587/25, SSL direkte på port 465 — det
    vanligste oppsettet hos de fleste e-postleverandører). Kaster EpostFeil med den underliggende
    feilmeldingen hvis noe går galt, i stedet for å late som sendingen lyktes."""
    mangler = [
        felt for felt in ("smtp_server", "bruker", "passord", "mottaker")
        if not innstillinger.get(felt)
    ]
    if mangler:
        raise EpostFeil(f"Mangler SMTP-innstilling(er): {', '.join(mangler)}.")

    melding = MIMEText(tekst, "plain", "utf-8")
    melding["Subject"] = emne
    melding["From"] = innstillinger["bruker"]
    melding["To"] = innstillinger["mottaker"]

    server, port = innstillinger["smtp_server"], int(innstillinger.get("smtp_port") or 587)
    try:
        if port == 465:
            klient = smtplib.SMTP_SSL(server, port, timeout=20)
        else:
            klient = smtplib.SMTP(server, port, timeout=20)
            klient.starttls()
        try:
            klient.login(innstillinger["bruker"], innstillinger["passord"])
            klient.sendmail(innstillinger["bruker"], [innstillinger["mottaker"]], melding.as_string())
        finally:
            klient.quit()
    except (smtplib.SMTPException, OSError) as e:
        raise EpostFeil(str(e)) from e