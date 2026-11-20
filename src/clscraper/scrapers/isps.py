"""Per-ISP settings. Only URLs + selectors differ; the engine lives in base.py.

!! These selectors are STARTING POINTS. ISP sites are redesigned often and A/B tested.
!! Before a real run, open each URL with `playwright codegen <url>`, enter a test
!! address, and paste the selectors it records at the FRONT of the lists below.
"""
from __future__ import annotations

import re

from .base import BaseISPScraper


class SpectrumScraper(BaseISPScraper):
    key, name = "spectrum", "Spectrum (Charter)"
    start_url = "https://www.spectrum.com/address/localization"
    # Spectrum's form has separate Street / Apt / ZIP fields
    address_input_selectors = [
        'input[name="address1"]', 'input[id*="address1" i]', 'input[aria-label*="street address" i]',
        *BaseISPScraper.address_input_selectors,
    ]
    unit_input_selectors = ['input[name="address2"]', 'input[id*="address2" i]',
                            *BaseISPScraper.unit_input_selectors]
    zip_input_selectors = ['input[name="zip"]', 'input[name="zipcode"]', *BaseISPScraper.zip_input_selectors]
    submit_selectors = ['button:has-text("Find Offers")', 'button:has-text("Check Availability")',
                        *BaseISPScraper.submit_selectors]

    def open_start_page(self) -> None:
        super().open_start_page()
        # A cookie from an earlier visit can send the residential page to Spectrum Business.
        if "spectrum business" in self.body_text().lower():
            self.page.context.clear_cookies()
            super().open_start_page()
        if "spectrum business" in self.body_text().lower():
            # Happens even in a fresh browser: Spectrum recognises the visitor's network
            # (e.g. a building / campus on a Spectrum Business line) and forces the Business site.
            raise RuntimeError("Spectrum redirected to Spectrum Business. It likely recognises this "
                               "network as a Spectrum Business connection. Try from another network "
                               "(e.g. phone hotspot) or check spectrum.com in your normal browser.")


class ATTScraper(BaseISPScraper):
    key, name = "att", "AT&T"
    start_url = "https://www.att.com/internet/"
    # Seen 2026-11: one box "12345 Main st Apt A Winter Park, FL 32792" + "Check availability" button
    address_input_selectors = ['#input-addressInput', 'input[aria-label="addressInput"]',
                               *BaseISPScraper.address_input_selectors]
    unit_input_selectors: list[str] = []      # apartment goes in the same box
    zip_input_selectors: list[str] = []
    submit_selectors = ['[data-testid="checkAvailabilityId"]', *BaseISPScraper.submit_selectors]
    open_form_selectors = ['button:has-text("Check availability")', 'a:has-text("Check availability")',
                           'button:has-text("Check your address")', *BaseISPScraper.open_form_selectors]
    label_link_patterns = [r"broadband\s*facts", r"view\s*broadband\s*facts", *BaseISPScraper.label_link_patterns]


class VerizonScraper(BaseISPScraper):
    key, name = "verizon", "Verizon (Fios / 5G Home)"
    start_url = "https://www.verizon.com/home/internet/"
    # Seen 2026-11: address box in the hero banner (web component) + yellow "Get started" button.
    # Verizon REQUIRES picking from its own dropdown ("Please ... select from the dropdown"),
    # e.g. "3147 Broadway Apt 17 Flr 5, New York, NY 10027".
    # Box says: "Type your address (without unit #) and select the one closest to your own."
    # Suggestions highlight matched text with <mark>; the unit box (#internal-wrapper-unit) comes after.
    address_input_selectors = ['#streetaddress', 'input[aria-label*="street address" i]',
                               *BaseISPScraper.address_input_selectors]
    suggestion_selectors = ['[role="option"]:has(mark)', 'li:has(mark)', 'button:has(mark)',
                            '*:has(> mark)', *BaseISPScraper.suggestion_selectors]
    unit_after_suggestion = True
    text_pick_suggestion = True
    keyboard_pick_suggestion = True
    unit_input_selectors = ['#internal-wrapper-unit input', '#unit', 'input[aria-label*="unit" i]',
                            'input[aria-label*="apt" i]', 'input[placeholder*="apt" i]',
                            'input[placeholder*="unit" i]', 'select[aria-label*="unit" i]']
    zip_input_selectors: list[str] = []
    submit_selectors = ['button.getStartedBtn:visible', 'button:has-text("Get started"):visible',
                        *BaseISPScraper.submit_selectors]
    open_form_selectors = ['button:has-text("Check availability")', 'a:has-text("Check availability")',
                           'button:has-text("Get started")', *BaseISPScraper.open_form_selectors]


class XfinityScraper(BaseISPScraper):
    key, name = "xfinity", "Xfinity (Comcast)"
    start_url = "https://www.xfinity.com/learn/offers"
    # Seen 2026-11: ONE box "Street address and apartment" (placeholder "1234 Main St, Apt J, ...")
    address_input_selectors = ['input[name="localizationAddressField"]', 'input[autocomplete="street-address"]',
                               *BaseISPScraper.address_input_selectors]
    unit_input_selectors: list[str] = []
    zip_input_selectors: list[str] = []
    open_form_selectors = ['button:has-text("Enter address")', 'button:has-text("Check availability")',
                           'a:has-text("Shop offers")', *BaseISPScraper.open_form_selectors]
    submit_selectors = ['button[aria-label="Check availability"]', 'button:has-text("See offers")',
                        'button:has-text("View offers")', *BaseISPScraper.submit_selectors]


class FrontierScraper(BaseISPScraper):
    key, name = "frontier", "Frontier"
    start_url = "https://frontier.com/shop/internet"
    # Seen 2026-11: input[name=street-address] "Enter your address to view plans in your area".
    # Frontier is now part of Verizon; the flow may hand over to verizon.com.
    address_input_selectors = ['input[name="street-address"]', *BaseISPScraper.address_input_selectors]
    unit_input_selectors: list[str] = []
    zip_input_selectors: list[str] = []
    open_form_selectors = ['button:has-text("Check availability")', 'a:has-text("Check availability")',
                           'button:has-text("Shop now")', *BaseISPScraper.open_form_selectors]


class CoxScraper(BaseISPScraper):
    key, name = "cox", "Cox"
    start_url = "https://www.cox.com/residential/internet.html"
    # Seen 2026-11: cox.com now shows Spectrum branding (Charter-Cox merger); the address form
    # opens from a "Check availability" link. Needs a codegen pass on a Cox-area address.
    open_form_selectors = ['button:has-text("Check availability")', 'a:has-text("Check availability")',
                           'a:has-text("Shop Cox Internet")', *BaseISPScraper.open_form_selectors]


SCRAPERS: dict[str, type[BaseISPScraper]] = {
    # Frontier/Cox classes are kept for reference but not registered: their sites now redirect
    # to Verizon / Spectrum (mergers), so those brands are checked via the parent ISP.
    cls.key: cls for cls in (ATTScraper, VerizonScraper, SpectrumScraper, XfinityScraper)
}


# --------------------------------------------------------------------------- every other provider
from urllib.parse import quote_plus  # noqa: E402

import yaml  # noqa: E402

from ..config import ROOT  # noqa: E402
from ..definitions import ISP_BRAND_PATTERNS  # noqa: E402

TUNED = set(SCRAPERS)                         # automatic + assisted
_KNOWN: list[tuple[str, str]] = []           # (pattern, key) from providers.yaml -> assisted only


def _load_known() -> None:
    path = ROOT / "providers.yaml"
    if not path.exists():
        return
    for p in (yaml.safe_load(path.read_text()) or {}).get("providers", []):
        SCRAPERS[p["key"]] = type(f"{p['key']}Scraper", (BaseISPScraper,), {
            "key": p["key"], "name": p["name"], "start_url": p["url"], "assisted_only": True})
        _KNOWN.append((p["patterns"], p["key"]))


_load_known()


# --------------------------------------------------------------------------- ISPs set up with `cls setup-isp`
SITES_FILE = ROOT / "isp_sites.yaml"
_SITE_FRNS: dict[str, str] = {}              # FRN -> key, for ISPs configured with the setup recorder
_SITE_PATTERNS: list[tuple[str, str]] = []   # (brand regex, key)


def load_sites() -> dict:
    return (yaml.safe_load(SITES_FILE.read_text()) or {}).get("sites", {}) if SITES_FILE.exists() else {}


def site_scraper(key: str, site: dict) -> type[BaseISPScraper]:
    """A scraper class from a recorded site configuration (recorded selectors first, defaults after)."""
    B = BaseISPScraper
    attrs = {
        "key": key, "name": site.get("name", key), "start_url": site["start_url"], "assisted_only": False,
        "open_form_selectors": list(site.get("open_form") or []) + B.open_form_selectors,
        "address_input_selectors": list(site.get("address_input") or []) + B.address_input_selectors,
        "suggestion_selectors": list(site.get("suggestion") or []) + B.suggestion_selectors,
        "submit_selectors": list(site.get("submit") or []) + B.submit_selectors,
        "label_link_patterns": list(site.get("label_links") or []) + B.label_link_patterns,
        "text_pick_suggestion": True, "keyboard_pick_suggestion": bool(site.get("suggestion")),
    }
    if site.get("single_line_address"):            # unit + city + ZIP typed into the one box
        attrs.update(unit_input_selectors=[], zip_input_selectors=[])
    return type(f"{key}SiteScraper", (BaseISPScraper,), attrs)


def register_sites() -> None:
    _SITE_FRNS.clear()
    _SITE_PATTERNS.clear()
    for key, site in load_sites().items():
        if not site.get("start_url"):
            continue
        SCRAPERS[key] = site_scraper(key, site)
        TUNED.add(key)
        for f in site.get("frns") or []:
            _SITE_FRNS[str(f).zfill(10)] = key
        if site.get("patterns"):
            _SITE_PATTERNS.append((site["patterns"], key))


register_sites()


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")[:40] or "provider"


def search_scraper(brand: str) -> str:
    """Register (once) an assisted-only scraper that starts at a web search for this brand."""
    key = "search_" + _slug(brand)
    if key not in SCRAPERS:
        q = quote_plus(f"{brand} internet check availability")
        SCRAPERS[key] = type(f"{key}Scraper", (BaseISPScraper,), {
            "key": key, "name": f"{brand} (via web search)", "assisted_only": True,
            "start_url": f"https://duckduckgo.com/?q={q}",
            "address_input_selectors": [],      # don't type the address into the search page
        })
    return key


def resolve_provider(brand: str | None, frn: str | None = None) -> tuple[str, str]:
    """NBM brand (+ FRN) -> (scraper key, kind) with kind in tuned | known | search."""
    if frn and str(frn).zfill(10) in _SITE_FRNS:
        return _SITE_FRNS[str(frn).zfill(10)], "tuned"
    for pat, key in _SITE_PATTERNS:
        if brand and re.search(pat, brand, re.I):
            return key, "tuned"
    for key, pat in ISP_BRAND_PATTERNS.items():
        if brand and re.search(pat, brand, re.I):
            return key, "tuned"
    for pat, key in _KNOWN:
        if brand and re.search(pat, brand, re.I):
            return key, "known"
    return search_scraper(brand or "unknown"), "search"


CHECK_LABEL = {"tuned": "✅ automatic + assisted", "known": "✅ assisted", "search": "🔎 assisted (web search)"}
