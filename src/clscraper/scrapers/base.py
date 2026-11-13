"""Generic ISP address-lookup + Broadband Facts harvester (Playwright, sync API).

How a lookup works (BaseISPScraper.run):
  1. open the ISP's address / availability page
  2. close cookie banners & pop-ups
  3. type the address (street + unit + ZIP, or one line), pick the autocomplete suggestion, submit
  4. wait until the plans page shows up (or an "unavailable"/"verify address"/bot-check page)
  5. harvest text from the page, hidden HTML, iframes, shadow DOM and JSON network responses
  6. click every "Broadband Facts" / "label" toggle and open label links, harvesting again
  7. regex out the Unique Plan Identifiers (see upi.py)
  8. optional manual assist: you finish the steps in the browser, press Enter, it harvests again

Each ISP subclass mostly just overrides `start_url` and selector lists. Sites change
often: use `playwright codegen <url>` to record the right selectors (see README).
"""
from __future__ import annotations

import re
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import Locator, Page
from playwright.sync_api import TimeoutError as PWTimeout

from ..label_parser import FIELDS as LABEL_FIELDS
from ..label_parser import enrich_upis
from ..upi import find_upis_in_many

# Collects visible text plus text inside open shadow roots (web components).
DEEP_TEXT_JS = """
() => {
  const parts = [document.body ? document.body.innerText : ''];
  const visit = (root) => {
    root.querySelectorAll('*').forEach(el => {
      if (el.shadowRoot) { parts.push(el.shadowRoot.textContent || ''); visit(el.shadowRoot); }
    });
  };
  visit(document);
  return parts.join('\\n');
}
"""


@dataclass
class Address:
    line1: str
    city: str = ""
    state: str = ""
    zip: str = ""
    unit: str = ""
    location_id: str = ""

    def one_line(self) -> str:
        street = f"{self.line1} {self.unit}".strip() if self.unit else self.line1
        tail = f"{self.state} {self.zip}".strip()
        return ", ".join(p for p in (street, self.city, tail) if p)

    @classmethod
    def parse(cls, text: str, location_id: str = "") -> "Address":
        """Parse '123 Main St Apt 4, Durham, NC 27701' (best effort)."""
        parts = [p.strip() for p in text.split(",") if p.strip()]
        line1 = parts[0] if parts else text
        unit = ""
        m = re.search(r"\s+((?:apt|unit|ste|suite|#)\s*\S+)$", line1, re.I)
        if m:
            unit, line1 = m.group(1), line1[: m.start()]
        city = state = zip_ = ""
        if len(parts) >= 2:
            m = re.search(r"\b([A-Za-z]{2})\b\s*(\d{5})?(?:-\d{4})?\s*$", parts[-1])
            if m:
                state, zip_ = m.group(1).upper(), m.group(2) or ""
                city = parts[-2] if len(parts) >= 3 else parts[-1][: m.start()].strip()
            else:
                city = parts[1]
        return cls(line1=line1, city=city, state=state, zip=zip_, unit=unit, location_id=location_id)


@dataclass
class ScrapeResult:
    isp: str
    address: str
    location_id: str = ""
    status: str = "pending"   # ok | no_upi_found | not_serviceable | address_issue | blocked | timeout | error
    upis: list[dict] = field(default_factory=list)
    final_url: str = ""
    message: str = ""
    screenshot: str = ""
    html: str = ""
    scraped_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))

    def rows(self, extra: dict | None = None) -> list[dict]:
        """Flatten to one CSV row per UPI (or one row with an empty UPI)."""
        base = {k: v for k, v in asdict(self).items() if k != "upis"}
        base.update(extra or {})
        expected_frns = set(filter(None, str(base.get("nbm_frns", "")).split(";")))
        hits = self.upis or [{}]
        out = []
        for h in hits:
            row = dict(base)
            row.update({
                "upi": h.get("upi", ""), "frn": h.get("frn", ""), "plan_code": h.get("plan_code", ""),
                "upi_source": h.get("source", ""), "context": h.get("context", ""),
            })
            for f in LABEL_FIELDS:
                row[f] = h.get(f, "")
            row["frn_matches_nbm"] = (h.get("frn") in expected_frns) if (h and expected_frns) else ""
            out.append(row)
        return out


class BaseISPScraper:
    key = "base"
    name = "Generic ISP"
    start_url = ""

    # Buttons/links that reveal the address form when it is not on the page yet
    open_form_selectors = [
        'button:has-text("Check availability")', 'a:has-text("Check availability")',
        'button:has-text("Check Availability")', 'a:has-text("Check Availability")',
        'button:has-text("Shop now")', 'a:has-text("See plans")', 'button:has-text("See plans")',
        'text=/enter (your )?address/i',
    ]
    address_input_selectors = [
        'input[autocomplete="address-line1"]', 'input[autocomplete*="street" i]',
        'input[name*="street" i]', 'input[id*="street" i]',
        'input[name="address"]', 'input[name="address1"]', 'input[id="address1"]',
        'input[placeholder*="street" i]', 'input[placeholder*="address" i]',
        'input[aria-label*="address" i]', 'input[name*="address" i]', 'input[id*="address" i]',
    ]
    unit_input_selectors = [
        'input[autocomplete="address-line2"]', 'input[name*="apt" i]', 'input[name*="unit" i]',
        'input[placeholder*="apt" i]', 'input[placeholder*="unit" i]', 'input[aria-label*="apt" i]',
        'input[name="address2"]', 'input[id="address2"]',
    ]
    zip_input_selectors = [
        'input[autocomplete="postal-code"]', 'input[name*="zip" i]', 'input[id*="zip" i]',
        'input[placeholder*="zip" i]', 'input[aria-label*="zip" i]',
    ]
    suggestion_selectors = [
        '[role="listbox"] [role="option"]', 'ul[role="listbox"] li', '.pac-item',
        '[class*="suggestion" i] li', '[class*="autocomplete" i] li', '[id*="suggestion" i] li',
        '[class*="typeahead" i] li', '[data-testid*="suggestion" i]',
    ]
    submit_selectors = [
        'button[type="submit"]:visible', 'button:has-text("Check availability")',
        'button:has-text("Check Availability")', 'button:has-text("Find offers")',
        'button:has-text("See offers")', 'button:has-text("Continue")', 'button:has-text("Submit")',
        'button:has-text("Go")',
    ]
    popup_close_selectors = [
        '#onetrust-accept-btn-handler', 'button:has-text("Accept All")', 'button:has-text("Accept all")',
        'button:has-text("Got it")', 'button:has-text("No thanks")', 'button:has-text("No, thanks")',
        '[aria-label="Close"]:visible', '[aria-label="close"]:visible', 'button.close:visible',
    ]
    label_link_patterns = [
        r"broadband\s*facts", r"broadband\s*(consumer\s*)?labels?", r"view\s*(the\s*)?labels?",
        r"plan\s*labels?", r"label\s*details",
    ]
    results_ready_patterns = [r"broadband\s*facts", r"\$\s?\d+(\.\d\d)?\s*/\s*mo", r"\bmbps\b", r"\bgig\b"]
    not_serviceable_patterns = [
        r"not available at (this|your) address", r"isn.t available (at|in)", r"no service(s)? available",
        r"outside (of )?our service area", r"we don.t (currently )?(offer|service)",
    ]
    address_issue_patterns = [
        r"we couldn.t find (that|your|this) address", r"(please )?verify your address",
        r"address (was )?not found", r"did you mean", r"select your address", r"choose your address",
    ]
    blocked_patterns = [
        r"access denied", r"are you a (human|robot)", r"verify you are (a )?human",
        r"unusual (traffic|activity)", r"press (and|&) hold", r"request (was )?blocked",
    ]

    def __init__(self, page: Page, *, timeout_ms: int = 45000, artifacts_dir: str | Path | None = None,
                 manual_assist: bool = False, max_label_clicks: int = 25, log=print):
        self.page = page
        self.timeout_ms = timeout_ms
        self.artifacts_dir = Path(artifacts_dir) if artifacts_dir else None
        self.manual_assist = manual_assist
        self.max_label_clicks = max_label_clicks
        self.log = log
        self._responses = []
        page.set_default_timeout(timeout_ms)
        page.on("response", self._on_response)

    # ------------------------------------------------------------------ helpers
    def _on_response(self, resp) -> None:
        try:
            if resp.request.resource_type in ("xhr", "fetch", "document"):
                ctype = (resp.headers or {}).get("content-type", "")
                if any(t in ctype for t in ("json", "text", "html", "javascript")):
                    self._responses.append(resp)
        except Exception:
            pass

    def first_visible(self, selectors: list[str], timeout_ms: int = 2500) -> Locator | None:
        """Return the first selector that is visible within timeout_ms (checked round-robin)."""
        deadline = time.time() + timeout_ms / 1000
        while True:
            for sel in selectors:
                try:
                    loc = self.page.locator(sel).first
                    if loc.count() and loc.is_visible():
                        return loc
                except Exception:
                    continue
            if time.time() >= deadline:
                return None
            self.page.wait_for_timeout(250)

    def body_text(self) -> str:
        try:
            return self.page.evaluate(DEEP_TEXT_JS) or ""
        except Exception:
            try:
                return self.page.inner_text("body")
            except Exception:
                return ""

    @staticmethod
    def _matches(patterns: list[str], text: str) -> bool:
        return any(re.search(p, text, re.I) for p in patterns)

    # ------------------------------------------------------------------ steps
    def open_start_page(self) -> None:
        self.page.goto(self.start_url, wait_until="domcontentloaded")
        try:
            self.page.wait_for_load_state("networkidle", timeout=10000)
        except PWTimeout:
            pass

    def dismiss_popups(self) -> None:
        for sel in self.popup_close_selectors:
            try:
                loc = self.page.locator(sel).first
                if loc.count() and loc.is_visible():
                    loc.click(timeout=1500)
                    self.page.wait_for_timeout(300)
            except Exception:
                continue

    def enter_address(self, addr: Address) -> None:
        field_ = self.first_visible(self.address_input_selectors, 3000)
        if field_ is None:  # address form hidden behind a "Check availability" button?
            opener = self.first_visible(self.open_form_selectors, 3000)
            if opener is None:
                raise RuntimeError("address input not found (update address_input_selectors)")
            opener.click()
            field_ = self.first_visible(self.address_input_selectors, 8000)
            if field_ is None:
                raise RuntimeError("address input not found after opening the form")

        zip_field = self.first_visible(self.zip_input_selectors, 800)
        separate_zip = zip_field is not None and addr.zip
        text = addr.line1 if separate_zip else addr.one_line()
        field_.click()
        field_.fill("")
        field_.type(text, delay=60)          # typing (not fill) triggers the autocomplete
        self.page.wait_for_timeout(1500)
        self.pick_suggestion(addr)

        if addr.unit:
            unit = self.first_visible(self.unit_input_selectors, 800)
            if unit is not None:
                unit.fill(addr.unit)
        if separate_zip:
            zip_field.fill(addr.zip)
        self.submit()

    def pick_suggestion(self, addr: Address) -> bool:
        sug = self.first_visible(self.suggestion_selectors, 3000)
        if sug is None:
            return False
        # Prefer the suggestion that contains the house number and the ZIP (or city)
        house = addr.line1.split()[0] if addr.line1 else ""
        for sel in self.suggestion_selectors:
            try:
                opts = self.page.locator(sel)
                if not (opts.count() and opts.first.is_visible()):
                    continue
                for i in range(min(opts.count(), 8)):
                    t = opts.nth(i).inner_text()
                    if house and house in t and (not addr.zip or addr.zip in t
                                                 or (addr.city and addr.city.lower() in t.lower())):
                        opts.nth(i).click()
                        return True
                break
            except Exception:
                continue
        sug.click()
        return True

    def submit(self) -> None:
        btn = self.first_visible(self.submit_selectors, 1500)
        if btn is not None:
            try:
                btn.click(timeout=5000)
                return
            except Exception:
                pass
        self.page.keyboard.press("Enter")

    def wait_for_results(self) -> str:
        """Poll the page until it looks like results / an error page. Returns the body text."""
        start_url = self.page.url
        deadline = time.time() + self.timeout_ms / 1000
        text = ""
        while time.time() < deadline:
            try:
                self.page.wait_for_load_state("domcontentloaded", timeout=5000)
            except PWTimeout:
                pass
            text = self.body_text()
            if (self._matches(self.blocked_patterns, text)
                    or self._matches(self.not_serviceable_patterns, text)
                    or self._matches(self.address_issue_patterns, text)
                    or (self._matches(self.results_ready_patterns, text) and self.page.url != start_url)):
                break
            self.page.wait_for_timeout(1000)
        try:
            self.page.wait_for_load_state("networkidle", timeout=8000)
        except PWTimeout:
            pass
        return self.body_text() or text

    def handle_address_issue(self, addr: Address) -> bool:
        """'Did you mean...?' pages: pick the closest suggested address and continue."""
        picked = self.pick_suggestion(addr)
        if not picked:
            radio = self.first_visible(['input[type="radio"]', '[role="radio"]'], 1500)
            if radio is None:
                return False
            radio.click()
        self.submit()
        self.wait_for_results()
        return True

    def classify(self, text: str) -> str:
        if self._matches(self.blocked_patterns, text):
            return "blocked"
        if self._matches(self.not_serviceable_patterns, text):
            return "not_serviceable"
        if self._matches(self.address_issue_patterns, text):
            return "address_issue"
        return "loaded"

    def harvest(self, include_network: bool = True) -> list[tuple[str, str]]:
        chunks: list[tuple[str, str]] = [("dom_text", self.body_text())]
        try:
            chunks.append(("html", self.page.content()))
        except Exception:
            pass
        for fr in self.page.frames[1:]:
            try:
                chunks.append((f"iframe:{fr.url[:80]}", fr.content()))
            except Exception:
                continue
        if include_network:
            for resp in list(self._responses):
                try:
                    body = resp.text()
                    if body and len(body) < 5_000_000:
                        chunks.append((f"network:{resp.url[:120]}", body))
                except Exception:
                    continue
        return chunks

    def open_labels(self) -> list[tuple[str, str]]:
        """Click label toggles (harvesting each modal) and visit label links."""
        chunks: list[tuple[str, str]] = []
        hrefs: list[str] = []
        clicks = 0
        here = self.page.url.split("#")[0]
        for pat in self.label_link_patterns:
            loc = self.page.get_by_text(re.compile(pat, re.I))
            try:
                n = loc.count()
            except Exception:
                continue
            for i in range(n):
                if clicks >= self.max_label_clicks:
                    break
                el = loc.nth(i)
                try:
                    if not el.is_visible():
                        continue
                    href = el.evaluate("e => { const a = e.closest('a'); return a ? a.href : ''; }") or ""
                    if href and not href.startswith("javascript") and href.split("#")[0] != here:
                        if href not in hrefs:
                            hrefs.append(href)
                        continue
                    el.scroll_into_view_if_needed(timeout=2000)
                    el.click(timeout=3000)
                    clicks += 1
                    self.page.wait_for_timeout(1200)
                    chunks += self.harvest(include_network=False)
                    if self.page.url.split("#")[0] != here:   # click navigated away
                        self.page.go_back()
                        self.page.wait_for_timeout(1500)
                    else:
                        self.page.keyboard.press("Escape")
                except Exception:
                    continue
        for href in hrefs[: self.max_label_clicks]:
            p = self.page.context.new_page()
            try:
                p.goto(href, wait_until="domcontentloaded", timeout=self.timeout_ms)
                try:
                    p.wait_for_load_state("networkidle", timeout=8000)
                except PWTimeout:
                    pass
                chunks.append((f"label_page:{href[:120]}", p.content()))
                chunks.append((f"label_page_text:{href[:120]}", p.inner_text("body")))
            except Exception:
                pass
            finally:
                p.close()
        return chunks

    def save_artifacts(self, res: ScrapeResult, addr: Address) -> None:
        if not self.artifacts_dir:
            return
        d = self.artifacts_dir / self.key
        d.mkdir(parents=True, exist_ok=True)
        slug = addr.location_id or re.sub(r"[^A-Za-z0-9]+", "_", addr.one_line())[:60]
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        try:
            png = d / f"{slug}_{stamp}.png"
            self.page.screenshot(path=str(png), full_page=True)
            res.screenshot = str(png)
            html = d / f"{slug}_{stamp}.html"
            html.write_text(self.page.content())
            res.html = str(html)
        except Exception:
            pass

    # ------------------------------------------------------------------ main
    def run(self, addr: Address) -> ScrapeResult:
        res = ScrapeResult(isp=self.key, address=addr.one_line(), location_id=addr.location_id)
        chunks: list[tuple[str, str]] = []
        state = "error"
        try:
            self.open_start_page()
            self.dismiss_popups()
            self.enter_address(addr)
            text = self.wait_for_results()
            state = self.classify(text)
            if state == "address_issue" and self.handle_address_issue(addr):
                state = self.classify(self.body_text())
            self.dismiss_popups()
            if state != "blocked":
                chunks += self.harvest()
                chunks += self.open_labels()
        except PWTimeout as e:
            state, res.message = "timeout", str(e)[:300]
        except Exception as e:  # noqa: BLE001 - record and move on
            state, res.message = "error", f"{type(e).__name__}: {e}"[:300]

        res.upis = find_upis_in_many(chunks)
        if not res.upis and self.manual_assist:
            self.log(f"\n[{self.name}] Automatic lookup ended with status '{state}'.\n"
                     f"  In the browser window: finish entering {addr.one_line()!r}, open the plans\n"
                     f"  and expand the Broadband Facts labels. Then press Enter here (or type 'skip').")
            if input("> ").strip().lower() != "skip":
                chunks = self.harvest() + self.open_labels()
                res.upis = find_upis_in_many(chunks)
                if res.upis:
                    res.message = (res.message + " | manual assist").strip(" |")
                state = self.classify(self.body_text()) if not res.upis else state

        res.upis = enrich_upis(res.upis, chunks)
        res.status = "ok" if res.upis else ("no_upi_found" if state == "loaded" else state)
        try:
            res.final_url = self.page.url
        except Exception:
            pass
        self.save_artifacts(res, addr)
        return res
