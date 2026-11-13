"""Find FCC Broadband Consumer Label "Unique Plan Identifiers" in page text / HTML / JSON.

Format (47 CFR 8.1(a), FCC 22-86): the letter "F", the provider's 10-digit FCC
Registration Number (FRN), then a provider-assigned alphanumeric of up to 15
characters, e.g. F0001234567ABC123XYZ.
"""
from __future__ import annotations

import html
import re

# Strict pattern: F + 10 digits + 1..15 alphanumerics, not glued to other alphanumerics.
UPI_RE = re.compile(r"(?<![A-Za-z0-9])F(\d{10})([A-Za-z0-9]{1,15})(?![A-Za-z0-9])")

# Looser pattern next to the printed caption, tolerating spaces / dashes inside the id.
LABELLED_RE = re.compile(
    r"unique\s*plan\s*identifier[\"'\s:]*?"
    r"(F[\s\-]?(?:\d[\s\-]?){10}[\s\-]?[A-Za-z0-9]{1,15})(?![A-Za-z0-9])",
    re.I,
)

_TAG_RE = re.compile(r"<[^>]+>")


def normalise(text: str) -> str:
    """Unescape HTML/JSON and strip tags so captions and values sit next to each other."""
    text = html.unescape(text)
    text = text.replace("\\u0026", "&").replace("\\/", "/")
    return _TAG_RE.sub(" ", text)


def _context(text: str, start: int, end: int, width: int = 250) -> str:
    snippet = text[max(0, start - width): end + width]
    return re.sub(r"\s+", " ", snippet).strip()


def find_upis(text: str, source: str = "") -> list[dict]:
    """Return a list of {upi, frn, plan_code, source, context} found in *text*.

    Searches both the tag-stripped text (captions next to values) and the raw text
    (ids hidden in HTML attributes or JSON payloads).
    """
    if not text:
        return []
    found: dict[str, dict] = {}
    clean = normalise(text)

    def add(upi: str, frn: str, code: str, hay: str, start: int, end: int) -> None:
        found.setdefault(upi, {
            "upi": upi, "frn": frn, "plan_code": code, "source": source,
            "context": _context(normalise(hay[max(0, start - 300): end + 300]), 0, 10**6, width=0),
        })

    for hay in (clean, text):
        for m in UPI_RE.finditer(hay):
            add(m.group(0), m.group(1), m.group(2), hay, m.start(), m.end())

    for m in LABELLED_RE.finditer(clean):
        candidate = re.sub(r"[\s\-]", "", m.group(1))
        mm = re.match(r"F(\d{10})([A-Za-z0-9]{1,15})", candidate)
        if mm:
            add(mm.group(0), mm.group(1), mm.group(2), clean, m.start(), m.end())
    return list(found.values())


def find_upis_in_many(chunks: list[tuple[str, str]]) -> list[dict]:
    """chunks: [(source, text)]. De-duplicates by UPI, preferring visible-text context."""
    merged: dict[str, dict] = {}
    for source, text in chunks:
        for hit in find_upis(text, source):
            prev = merged.get(hit["upi"])
            if prev is None or (prev["source"] != "dom_text" and source == "dom_text"):
                if prev:
                    hit["also_seen_in"] = prev.get("source")
                merged[hit["upi"]] = hit
    return sorted(merged.values(), key=lambda h: h["upi"])
