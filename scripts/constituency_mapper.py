"""Map classified items to a Westminster constituency (2024 boundaries, 650 seats)
and current MP. Free open APIs, cached in SQLite:
  - postcodes.io /places (geocode an area name) + reverse lookup -> constituency
  - members-api.parliament.uk -> current MP for a constituency
If an area can't be resolved, the item keeps its city label — still useful in the brief.
"""
import logging
from datetime import datetime, timedelta

import requests
from typing import Optional, List, Dict

log = logging.getLogger("mapper")
POSTCODES = "https://api.postcodes.io"
MEMBERS = "https://members-api.parliament.uk/api"
CACHE_DAYS = 7


def _cached(conn, table, key_col, key, val_col):
    row = conn.execute(
        f"SELECT {val_col}, fetched_at FROM {table} WHERE {key_col}=?", (key,)
    ).fetchone()
    if row and row["fetched_at"] > (datetime.utcnow() - timedelta(days=CACHE_DAYS)).isoformat():
        return row[val_col]
    return None


def area_to_constituency(conn, area_text: str, city: str) -> Optional[str]:
    """Geocode 'High Street, Croydon'-style text to a constituency."""
    if not area_text:
        return None
    key = f"{area_text}|{city}".lower()
    cached = _cached(conn, "area_cache", "area_text", key, "constituency")
    if cached is not None:
        return cached or None
    constituency = None
    try:
        q = requests.get(f"{POSTCODES}/places", params={"q": f"{area_text}", "limit": 5}, timeout=15).json()
        results = q.get("result") or []
        # prefer a hit in the right city/region
        best = next((r for r in results if city.split(" (")[0].lower()
                     in (str(r.get("county_unitary")) + str(r.get("region")) + str(r.get("name_1"))).lower()),
                    results[0] if results else None)
        if best:
            rev = requests.get(f"{POSTCODES}/postcodes",
                               params={"lon": best["longitude"], "lat": best["latitude"], "limit": 1},
                               timeout=15).json()
            hits = rev.get("result") or []
            if hits:
                constituency = hits[0].get("parliamentary_constituency_2024") or \
                               hits[0].get("parliamentary_constituency")
    except Exception as e:
        log.warning("geocode failed for %r: %s", area_text, e)
    conn.execute("INSERT OR REPLACE INTO area_cache VALUES (?,?,datetime('now'))",
                 (key, constituency or ""))
    conn.commit()
    return constituency


def mp_for_constituency(conn, constituency: str) -> str:
    if not constituency:
        return ""
    cached = _cached(conn, "mp_cache", "constituency", constituency, "mp_name")
    if cached is not None:
        return cached
    mp_name, party = "", ""
    try:
        r = requests.get(f"{MEMBERS}/Location/Constituency/Search",
                         params={"searchText": constituency, "take": 1}, timeout=15).json()
        items = r.get("items") or []
        if items:
            rep = (items[0]["value"].get("currentRepresentation") or {}).get("member", {}).get("value", {})
            mp_name = rep.get("nameDisplayAs", "")
            party = (rep.get("latestParty") or {}).get("name", "")
    except Exception as e:
        log.warning("MP lookup failed for %r: %s", constituency, e)
    conn.execute("INSERT OR REPLACE INTO mp_cache VALUES (?,?,?,datetime('now'))",
                 (constituency, mp_name, party))
    conn.commit()
    return mp_name


def map_items(conn, items: List[Dict]) -> List[Dict]:
    for it in items:
        constituency = area_to_constituency(conn, it.get("area", ""), it.get("city", ""))
        it["constituency"] = constituency or f"{it.get('city','')} (constituency unresolved)"
        it["mp_name"] = mp_for_constituency(conn, constituency) if constituency else ""
    return items