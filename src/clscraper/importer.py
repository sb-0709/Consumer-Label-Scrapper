"""Read Broadband Facts labels from pages the user saved in their OWN browser.

For sites whose bot protection blocks any automated browser: look up the address
yourself in normal Chrome, open the plan labels, save the page (Cmd+S, either
"Webpage, Complete" .html or "Webpage, Single File" .mhtml) or copy the label text,
then import it here. Same UPI and label parsing as the scraper.
"""
from __future__ import annotations

import email
import re
from email import policy

from .label_parser import enrich_upis
from .scrapers.base import Address, ScrapeResult
from .upi import find_upis_in_many


def file_to_chunks(name: str, data: bytes) -> list[tuple[str, str]]:
    """Saved page -> [(source, text)]. Handles .html/.htm/.txt and Chrome's .mhtml."""
    lower = name.lower()
    if lower.endswith((".mhtml", ".mht")) or data[:200].lstrip().lower().startswith(b"from:") \
            or b"multipart/related" in data[:2000].lower():
        msg = email.message_from_bytes(data, policy=policy.default)
        chunks = []
        for part in msg.walk():
            if part.get_content_type() in ("text/html", "text/plain"):
                try:
                    chunks.append((f"import:{name}", part.get_content()))
                except Exception:
                    payload = part.get_payload(decode=True) or b""
                    chunks.append((f"import:{name}", payload.decode("utf-8", "ignore")))
        return chunks
    return [(f"import:{name}", data.decode("utf-8", "ignore"))]


def import_labels(isp: str, addr: Address, chunks: list[tuple[str, str]]) -> ScrapeResult:
    res = ScrapeResult(isp=isp, address=addr.one_line(), location_id=addr.location_id)
    # visible-text style parsing works best on tag-stripped text; label_parser normalises HTML itself
    res.upis = enrich_upis(find_upis_in_many(chunks), chunks)
    res.status = "ok" if res.upis else "no_upi_found"
    names = sorted({re.sub(r"^import:", "", s) for s, _ in chunks})
    res.message = "imported from your browser: " + ", ".join(names)[:200]
    return res
