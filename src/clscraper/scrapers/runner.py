"""Run scrapers for one address or for the whole sampled task list (resumable)."""
from __future__ import annotations

import csv
import random
import time
from collections import defaultdict
from contextlib import contextmanager
from pathlib import Path

import pandas as pd

from ..config import ROOT, output_dir
from ..label_parser import FIELDS as LABEL_FIELDS
from .base import Address, ScrapeResult
from .isps import SCRAPERS

RESULT_FIELDS = [
    "scraped_at", "grp", "block_geoid", "location_id", "isp", "address", "status",
    "upi", "frn", "plan_code", "frn_matches_nbm", "nbm_frns", "nbm_brand", "nbm_technologies",
    *[f for f in LABEL_FIELDS if f != "label_text"],
    "upi_source", "context", "final_url", "message", "screenshot", "html", "label_text",
]
FINAL_STATUSES = {"ok", "not_serviceable"}   # not retried on resume


@contextmanager
def browser_context(cfg: dict, isp: str, headless: bool | None = None):
    """A fresh browser per ISP by default (no leftover cookies, e.g. Spectrum's Business redirect).

    Set scraper.persistent_profiles: true to keep one profile per ISP in .browser_profiles/.
    """
    from playwright.sync_api import sync_playwright

    s = cfg["scraper"]
    opts = dict(headless=s["headless"] if headless is None else headless,
                slow_mo=s.get("slow_mo_ms", 0), channel=s.get("browser_channel") or None)
    ctx_opts = dict(viewport={"width": 1366, "height": 900}, locale="en-US")
    with sync_playwright() as pw:
        browser = None
        if s.get("persistent_profiles"):
            profile = ROOT / ".browser_profiles" / isp
            profile.mkdir(parents=True, exist_ok=True)
            ctx = pw.chromium.launch_persistent_context(str(profile), **opts, **ctx_opts)
        else:
            browser = pw.chromium.launch(**opts)
            ctx = browser.new_context(**ctx_opts)
        try:
            yield ctx
        finally:
            for closer in (ctx.close, browser.close if browser else None):
                try:
                    if closer:
                        closer()
                except Exception:
                    pass


def _scrape_one(ctx, cfg: dict, isp: str, addr: Address, manual: bool, log,
                assist: bool = False, on_update=None) -> ScrapeResult:
    s = cfg["scraper"]
    tracing = bool(s.get("trace"))
    if tracing:   # step-by-step screenshots + DOM snapshots: `playwright show-trace <file>`
        ctx.tracing.start(screenshots=True, snapshots=True)
    page = ctx.new_page()
    try:
        scraper = SCRAPERS[isp](
            page, timeout_ms=s["timeout_ms"], manual_assist=manual,
            max_label_clicks=s.get("max_label_clicks", 25),
            artifacts_dir=(output_dir(cfg) / "artifacts") if s.get("save_artifacts") else None,
            log=log,
        )
        if assist:
            return scraper.assist(addr, max_minutes=s.get("assist_max_minutes", 10), on_update=on_update)
        return scraper.run(addr)
    finally:
        if not page.is_closed():
            page.close()
        if tracing:
            import re as _re
            from datetime import datetime as _dt
            d = output_dir(cfg) / "traces"
            d.mkdir(parents=True, exist_ok=True)
            slug = _re.sub(r"[^A-Za-z0-9]+", "_", addr.one_line())[:50]
            try:
                ctx.tracing.stop(path=str(d / f"{isp}_{slug}_{_dt.now():%Y%m%d-%H%M%S}.zip"))
            except Exception:
                pass


def scrape_one_isp(cfg: dict, addr: Address, isp: str, *, headless: bool | None = None,
                   manual: bool = False, assist: bool = False, on_update=None, log=print) -> ScrapeResult:
    """One ISP, one address, in its own browser (safe to call from a worker thread)."""
    if not assist and getattr(SCRAPERS.get(isp), "assisted_only", False):
        return ScrapeResult(isp=isp, address=addr.one_line(), location_id=addr.location_id,
                            status="needs_assisted",
                            message="No automatic website check for this provider yet.")
    try:
        with browser_context(cfg, isp, False if assist else headless) as ctx:
            return _scrape_one(ctx, cfg, isp, addr, manual, log, assist=assist, on_update=on_update)
    except Exception as e:  # noqa: BLE001 - e.g. browser failed to start
        return ScrapeResult(isp=isp, address=addr.one_line(), location_id=addr.location_id,
                            status="error", message=f"{type(e).__name__}: {e}"[:300])


def scrape_address(cfg: dict, addr: Address, isps: list[str], *, headless: bool | None = None,
                   manual: bool = False, parallel: int = 1, log=print, on_done=None,
                   assist: bool = False, on_start=None, on_update=None) -> list[ScrapeResult]:
    """Check several ISPs for one address; parallel>1 runs that many browsers at once."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    results: list[ScrapeResult] = []
    if parallel <= 1 or manual or assist:
        for isp in isps:
            log(f"[{isp}] {addr.one_line()}")
            if on_start:
                on_start(isp)
            res = scrape_one_isp(cfg, addr, isp, headless=headless, manual=manual, assist=assist,
                                 on_update=on_update, log=log)
            log(f"   -> {res.status}: {len(res.upis)} plan(s) {res.message}")
            results.append(res)
            if on_done:
                on_done(res)
        return results
    with ThreadPoolExecutor(max_workers=parallel) as pool:
        futures = {pool.submit(scrape_one_isp, cfg, addr, isp, headless=headless, log=lambda *_: None): isp
                   for isp in isps}
        for fut in as_completed(futures):
            res = fut.result()
            log(f"[{res.isp}] -> {res.status}: {len(res.upis)} plan(s) {res.message}")
            results.append(res)
            if on_done:
                on_done(res)
    order = {k: i for i, k in enumerate(isps)}
    return sorted(results, key=lambda r: order.get(r.isp, 99))


def _done_keys(path: Path) -> set[tuple[str, str]]:
    if not path.exists():
        return set()
    df = pd.read_csv(path, dtype=str)
    df = df[df["status"].isin(FINAL_STATUSES)]
    return set(zip(df["location_id"], df["isp"]))


def append_rows(path: Path, rows: list[dict]) -> None:
    new = not path.exists()
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=RESULT_FIELDS, extrasaction="ignore")
        if new:
            w.writeheader()
        w.writerows(rows)


def scrape_tasks(cfg: dict, tasks: pd.DataFrame, *, isps: list[str] | None = None,
                 manual: bool = False, headless: bool | None = None, resume: bool = True,
                 limit: int | None = None, log=print, progress=None, assist: bool = False) -> Path:
    """Run every (location, ISP) task; results are appended to outputs/scrape_results.csv."""
    out = output_dir(cfg) / "scrape_results.csv"
    done = _done_keys(out) if resume else set()
    tasks = tasks.astype(str)
    if isps:
        tasks = tasks[tasks["isp"].isin(isps)]
    todo = [t for t in tasks.to_dict("records") if (t["location_id"], t["isp"]) not in done]
    if limit:
        todo = todo[:limit]
    log(f"{len(todo)} lookups to run ({len(done)} already done) -> {out}")

    by_isp: dict[str, list[dict]] = defaultdict(list)
    for t in todo:
        by_isp[t["isp"]].append(t)
    lo, hi = cfg["scraper"].get("delay_between_requests_s", [8, 20])
    n_done = 0
    for isp, group in by_isp.items():
        if isp not in SCRAPERS and isp.startswith("search_"):
            from .isps import search_scraper
            search_scraper(group[0].get("nbm_brand") or isp[7:])
        if isp not in SCRAPERS:
            log(f"skip unknown ISP {isp}")
            continue
        for t in group:
            addr = Address(line1=t["address_primary"], city=t["city"], state=t["state"],
                           zip=t["zip"], location_id=t["location_id"])
            log(f"[{isp}] {t['grp']} {t['location_id']} {addr.one_line()}")
            res = scrape_one_isp(cfg, addr, isp, headless=headless, manual=manual, assist=assist, log=log)
            log(f"   -> {res.status}: {', '.join(u['upi'] for u in res.upis) or '-'} {res.message}")
            append_rows(out, res.rows(extra={k: t.get(k, "") for k in
                                             ("grp", "block_geoid", "nbm_frns", "nbm_brand", "nbm_technologies")}))
            n_done += 1
            if progress:
                progress(n_done, len(todo), res)
            if not assist:          # in assisted mode you are the pacing
                time.sleep(random.uniform(lo, hi))
    return out
