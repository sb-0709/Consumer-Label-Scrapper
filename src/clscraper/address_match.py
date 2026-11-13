"""Match a typed address to Fabric locations (USPS-style normalisation + fuzzy ranking)."""
from __future__ import annotations

import re
from difflib import SequenceMatcher

ABBREV = {
    "STREET": "ST", "AVENUE": "AVE", "AV": "AVE", "ROAD": "RD", "DRIVE": "DR", "LANE": "LN",
    "COURT": "CT", "BOULEVARD": "BLVD", "PLACE": "PL", "TERRACE": "TER", "CIRCLE": "CIR",
    "PARKWAY": "PKWY", "HIGHWAY": "HWY", "WAY": "WAY", "SQUARE": "SQ", "TRAIL": "TRL",
    "PIKE": "PIKE", "TURNPIKE": "TPKE", "EXPRESSWAY": "EXPY", "CRESCENT": "CRES",
    "NORTH": "N", "SOUTH": "S", "EAST": "E", "WEST": "W",
    "NORTHEAST": "NE", "NORTHWEST": "NW", "SOUTHEAST": "SE", "SOUTHWEST": "SW",
    "APARTMENT": "APT", "SUITE": "STE", "UNIT": "UNIT", "#": "UNIT",
}


def normalise_street(s: str) -> str:
    s = re.sub(r"[.,]", " ", (s or "").upper())
    s = re.sub(r"#\s*", "UNIT ", s)
    return " ".join(ABBREV.get(w, w) for w in s.split())


def house_number(s: str) -> str:
    m = re.match(r"\s*(\d+[A-Z]?)\b", (s or "").upper())
    return m.group(1) if m else ""


def rank_candidates(street: str, candidates: list[dict], top: int = 10) -> list[dict]:
    target = normalise_street(street)
    for c in candidates:
        c["match_score"] = round(SequenceMatcher(None, target, normalise_street(c["address_primary"])).ratio(), 3)
    return sorted(candidates, key=lambda c: -c["match_score"])[:top]
