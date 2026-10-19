"""Engangs-testscript: sjekker om Miljødirektoratets EGET interne API for høringslisten
(/hearingslistpage/gethearings — funnet ved å lese gjennom nettsidens egen JavaScript-bundle,
IKKE en gjetning) svarer på et rent `requests`-kall, uten Playwright/nettleser i det hele tatt.

Kjør lokalt:  python3 test_api_direkte.py

Hva den gjør:
1. Henter side 1 (skip=0) av «Olje og gass»-kategorien (subjectids=6169), BÅDE åpne og
   avsluttede høringer (searchExpired=true), og skriver ut hvor mange den fant.
2. Leter etter Nova/Harbour Energy/Transocean i resultatet (uansett hvilken side de ligger på),
   ved å bla gjennom med skip=0,20,40,... til den ikke finner flere.
3. Skriver ut alle funnet titler + saksnummer + frist, slik vi kan se om dette er brukbart i
   stedet for den skjøre klikk-baserte paginering.
"""
import requests

BASE = "https://www.miljodirektoratet.no"
API_URL = f"{BASE}/hearingslistpage/gethearings"
OLJE_OG_GASS_ID = 6169

headers = {
    "User-Agent": "Mozilla/5.0 (compatible; Sokkelradar/1.0; privat ikke-kommersielt verktøy)",
    "Accept": "application/json",
    "Referer": f"{BASE}/hoeringer/avsluttede-hoeringer/",
}

alle = []
skip = 0
for side_nr in range(1, 50):  # sikkerhetsgrense, samme ånd som maks_sider i miljodir_skrap.py
    try:
        svar = requests.get(
            API_URL,
            params={"skip": skip, "subjectids": OLJE_OG_GASS_ID, "searchTerm": "", "searchExpired": "true"},
            headers=headers, timeout=15,
        )
    except Exception as e:
        print(f"Side {side_nr} (skip={skip}): NETTVERKSFEIL: {e}")
        break

    print(f"Side {side_nr} (skip={skip}): HTTP {svar.status_code}")
    if svar.status_code != 200:
        print("  Svarte ikke med 200 — her er de første 500 tegnene av svaret:")
        print("  " + svar.text[:500].replace("\n", " "))
        break

    try:
        data = svar.json()
    except Exception:
        print("  Svaret var ikke gyldig JSON. Første 500 tegn:")
        print("  " + svar.text[:500].replace("\n", " "))
        break

    treff = data.get("hearings", [])
    totalt = data.get("totalMatching")
    print(f"  Fikk {len(treff)} høringer denne siden. totalMatching (ifølge API-et): {totalt}")
    if not treff:
        break
    alle.extend(treff)
    skip += 20
    if totalt is not None and skip >= totalt:
        break

print(f"\n=== Totalt samlet: {len(alle)} høringer i kategorien «Olje og gass» (åpne + avsluttede) ===\n")

nova_treff = [h for h in alle if any(
    s in h.get("title", "").lower() for s in ("nova", "harbour energy", "transocean", "35/9")
)]
if nova_treff:
    print(f"*** FANT {len(nova_treff)} TREFF SOM SER UT TIL Å GJELDE NOVA: ***")
    for h in nova_treff:
        print(f"  - {h.get('title')}  (saksnummer {h.get('hearingNumber')}, frist {h.get('deadline')})")
        print(f"    {BASE}{h.get('url')}")
else:
    print("Ingen umiddelbar Nova-match i tittel/saksnummer blant disse. Under er ALLE funnet, sjekk selv:")

print("\n=== Alle titler funnet (nyeste publisert øverst, som API-et selv sorterer) ===")
for h in alle:
    print(f"- [{h.get('published')}] {h.get('title')}  (frist {h.get('deadline')}, sak {h.get('hearingNumber')})")