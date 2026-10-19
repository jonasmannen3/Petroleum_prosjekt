"""Avstandsberegning mellom to posisjoner (lat, lon) — ren matte, ingen nettverk eller
Streamlit-avhengighet, så dette kan testes og gjenbrukes helt for seg selv. Brukt av
fane_horinger.py til å anslå hvor langt en rigg har igjen til et felt nevnt i en høring.
"""
from __future__ import annotations

import math

JORDRADIUS_KM = 6371.0


def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Storsirkel-avstand («som kråka flyr») mellom to (lat, lon)-par, i kilometer. IKKE en
    seilingsrute — bare en rett linje, til bruk som et grovt avstandsestimat. Samme antakelse
    som destinasjon.py/posisjon.py sine linjer på kartet gjør allerede, bare som et tall i
    stedet for en tegnet strek."""
    lat1, lon1 = math.radians(a[0]), math.radians(a[1])
    lat2, lon2 = math.radians(b[0]), math.radians(b[1])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    h = min(1.0, max(0.0, h))  # klipper av flyttall-støy rett over/under grensene til asin()
    return 2 * JORDRADIUS_KM * math.asin(math.sqrt(h))


def estimert_reisetid_timer(avstand_km: float, fart_knop: float | None) -> float | None:
    """Grovt reisetidsestimat basert på NÅVÆRENDE fart (knop) — ikke pålitelig hvis riggen ligger
    stille eller fartsmeldingen er lav fordi den f.eks. borer akkurat nå. Returnerer None når fart
    mangler eller er under 1 knop, i stedet for å vise et villedende høyt eller uendelig tall."""
    if not fart_knop or fart_knop < 1.0:
        return None
    fart_kmt = fart_knop * 1.852  # 1 knop = 1.852 km/t
    return avstand_km / fart_kmt