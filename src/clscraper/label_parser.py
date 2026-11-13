"""Pull the FCC Broadband Facts fields that sit next to each Unique Plan Identifier.

The FCC label template has fixed captions (Monthly Price, Typical Download Speed, ...),
so we cut out the text of each label (from the preceding "Broadband Facts" heading
to just after its UPI) and read the value after each caption. Works on visible text,
tag-stripped HTML and JSON payloads ("typicalDownloadSpeed":"300").
"""
from __future__ import annotations

import re

from .upi import normalise

SEP = r"[\W_]{0,12}"           # punctuation / quotes / colons between caption and value
MONEY = r"\$?\s*([\d,]+(?:\.\d{1,2})?)"

PATTERNS = {
    "plan_name": rf"broadband\s*facts{SEP}(.{{2,160}}?){SEP}fixed\s*broadband\s*consumer\s*disclosure",
    "monthly_price": rf"monthly\s*price{SEP}{MONEY}",
    "intro_months": r"(?:introductory|promotional)[^$]{0,120}?(\d{1,2})\s*(?:-\s*)?months?",
    "price_after_intro": rf"(?:after|following)[^$]{{0,80}}?(?:price|rate)[^$]{{0,40}}?{MONEY}",
    "contract": rf"(?:length\s*of\s*)?contract(?:\s*length)?{SEP}"
                r"(\d+\s*(?:-\s*)?(?:months?|years?|mo)|none|no\s*contract|not\s*required|month[-\s]to[-\s]month|n/?a)",
    "provider_monthly_fees": r"provider\s*monthly\s*fees(.{0,300}?)(?=one[-\s]?time|early\s*termination|government\s*taxes|$)",
    "one_time_fees": r"one[-\s]?time\s*(?:fees|charges)(?:\s*at\s*the\s*time\s*of\s*purchase)?(.{0,300}?)(?=early\s*termination|government\s*taxes|discounts|$)",
    "early_termination_fee": rf"early\s*termination\s*fee{SEP}(\$\s*[\d,.]+|none|n/?a|not\s*applicable)",
    # values can be ranges on the label, e.g. "89-346 Mbps", "40-69 Milliseconds"
    "typical_download": r"typical\s*download\s*speed[\W_]{0,12}([\d,.]+)(?:\s*[-\u2013to]+\s*([\d,.]+))?\s*(gbps|mbps|kbps)?",
    "typical_upload": r"typical\s*upload\s*speed[\W_]{0,12}([\d,.]+)(?:\s*[-\u2013to]+\s*([\d,.]+))?\s*(gbps|mbps|kbps)?",
    "typical_latency_ms": r"typical\s*latency[\W_]{0,12}([\d.]+(?:\s*[-\u2013]\s*[\d.]+)?)\s*(?:ms|milliseconds)?",
    "data_included": rf"data\s*included(?:\s*with\s*monthly\s*price)?{SEP}(unlimited|[\d,.]+\s*(?:gb|tb))",
}
FIELDS = [f + "_mbps" if f in ("typical_download", "typical_upload") else f for f in PATTERNS] + ["label_text"]


def _speed(m: re.Match) -> str:
    unit = (m.group(3) or "mbps").lower()
    factor = 1000 if unit == "gbps" else 0.001 if unit == "kbps" else 1
    vals = [f"{float(v.replace(',', '')) * factor:g}" for v in (m.group(1), m.group(2)) if v]
    return "-".join(vals)


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip(" :-|\"'")


def parse_label(text: str) -> dict:
    out = {}
    for field, pat in PATTERNS.items():
        m = re.search(pat, text, re.I | re.S)
        if not m:
            continue
        if field in ("typical_download", "typical_upload"):
            out[field + "_mbps"] = _speed(m)
        else:
            out[field] = _clean(m.group(1))[:200]
    return out


_CSS_RE = re.compile(r"[.#][\w-]+\s*\{[^}]*\}")


def label_window(text: str, upi: str) -> str | None:
    """Text of the label that ends with this UPI (from its 'Broadband Facts' heading)."""
    pos = text.find(upi)
    if pos < 0:
        return None
    start = max(0, pos - 4000)
    head = text.lower().rfind("broadband facts", start, pos)
    if head >= 0:
        start = head
    prev = list(re.finditer(r"F\d{10}[A-Za-z0-9]{1,15}", text[start:pos]))   # another label's UPI
    if prev:
        start = start + prev[-1].end()
    return text[start: pos + len(upi) + 300]


def enrich_upis(upis: list[dict], chunks: list[tuple[str, str]]) -> list[dict]:
    """Add label fields to each UPI hit, preferring visible text and label pages over raw HTML."""
    def priority(src: str) -> int:
        return 0 if src.startswith("label_page_text") else 1 if src == "dom_text" else 2

    prepared = [(src, txt if src in ("dom_text",) or src.startswith("label_page_text") else normalise(txt))
                for src, txt in sorted(chunks, key=lambda c: priority(c[0])) if txt]
    for hit in upis:
        fields: dict = {}
        for src, txt in prepared:
            win = label_window(txt, hit["upi"])
            if not win:
                continue
            for k, v in parse_label(win).items():
                fields.setdefault(k, v)
            fields.setdefault("label_text", re.sub(r"\s+", " ", _CSS_RE.sub(" ", win)).strip()[:3000])
        hit.update(fields)
    return upis
