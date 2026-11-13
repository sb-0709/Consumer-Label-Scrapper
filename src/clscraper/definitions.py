"""Competition definitions and ISP <-> NBM brand matching (mirrors the SQL)."""
from __future__ import annotations

import re

# BDC technology codes
TECH_NAMES = {
    0: "Other", 10: "Copper/DSL", 40: "Cable", 50: "Fiber",
    60: "GSO satellite", 61: "NGSO (LEO) satellite",
    70: "Unlicensed fixed wireless", 71: "Licensed fixed wireless", 72: "Licensed-by-rule fixed wireless",
}

DEFINITIONS = {
    "terrestrial": ("100/20 excluding satellite (60, 61)", lambda t: t not in (60, 61)),
    "wired":       ("100/20 wired: copper, cable, fiber (10, 40, 50)", lambda t: t in (10, 40, 50)),
    "fiber_fw":    ("100/20 fiber + fixed wireless (50, 70, 71, 72)", lambda t: t in (50, 70, 71, 72)),
    "fiber":       ("100/20 fiber only (50)", lambda t: t == 50),
}

# Scraper key -> regex on the NBM brand_name (provider_id lists differ by holding company,
# so brand matching is more robust; extend as needed).
ISP_BRAND_PATTERNS = {
    "att":      r"\bAT\s*&\s*T\b|\bAT and T\b",
    # Frontier is now part of Verizon and Cox part of Charter (Spectrum); their sites hand off,
    # so NBM rows for those brands are checked on the parent's site.
    "verizon":  r"verizon|\bfios\b|frontier",
    "spectrum": r"spectrum|charter|\bcox\b",
    "xfinity":  r"xfinity|comcast",
}


def match_isp(brand_name: str | None) -> str | None:
    if not brand_name:
        return None
    for key, pat in ISP_BRAND_PATTERNS.items():
        if re.search(pat, brand_name, re.I):
            return key
    return None


def qualifies(provider: dict, definition: str, min_down: float, min_up: float) -> bool:
    test = DEFINITIONS[definition][1]
    try:
        return (
            test(int(provider["technology"]))
            and float(provider["down"] or 0) >= min_down
            and float(provider["up"] or 0) >= min_up
        )
    except (KeyError, TypeError, ValueError):
        return False


# USPS code -> state name used in the NBM hive partition folders ("North Carolina")
STATE_NAMES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
    "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois",
    "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana",
    "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon",
    "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota",
    "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia",
    "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
    "PR": "Puerto Rico", "VI": "Virgin Islands", "GU": "Guam", "AS": "American Samoa",
    "MP": "Northern Mariana Islands",
}
