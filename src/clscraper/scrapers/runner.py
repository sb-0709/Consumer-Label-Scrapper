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
    """One persistent Chromium profile per ISP (keeps cookies / consent between lookups)."""
    from playwright.sync_api import sync_playwright

    s = cfg["scraper"]
    profile = ROOT / ".browser_profiles" / isp
    profile.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            str(profile),
            channel=s.get("browser_channel") or None,
            headless=s["headless"] if headless is None else headless,
            slow_mo=s.get("slow_mo_ms", 0),
            viewport={"width": 1366, "height": 900},
            locale="en-US",
        )
        try:
            yield ctx
        finally:
            ctx.close()


def _scrape_one(ctx, cfg: dict, isp: str, addr: Address, manual: bool, log) -> ScrapeResult:
    s = cfg["scraper"]
    page = ctx.new_page()
    try:
        scraper = SCRAPERS[isp](
            page, timeout_ms=s["timeout_ms"], manual_assist=manual,
            max_label_clicks=s.get("max_label_clicks", 25),
            artifacts_dir=(output_dir(cfg) / "artifacts") if s.get("save_artifacts") else None,
            log=log,
        )
        return scraper.run(addr)
    finally:
        page.close()


def scrape_one_isp(cfg: dict, addr: Address, isp: str, *, headless: bool | None = None,
                   manual: bool = False, log=print) -> ScrapeResult:
    """One ISP, one address, in its own browser (safe to call from a worker thread)."""
    try:
        with browser_context(cfg, isp, headless) as ctx:
            return _scrape_one(ctx, cfg, isp, addr, manual, log)
    except Exception as e:  # noqa: BLE001 - e.g. browser failed to start
        return ScrapeResult(isp=isp, address=addr.one_line(), location_id=addr.location_id,
                            status="error", message=f"{type(e).__name__}: {e}"[:300])


def scrape_address(cfg: dict, addr: Address, isps: list[str], *, headless: bool | None = None,
                   manual: bool = False, parallel: int = 1, log=print, on_done=None) -> list[ScrapeResult]:
    """Check several ISPs for one address; parallel>1 runs that many browsers at once."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    results: list[ScrapeResult] = []
    if parallel <= 1 or manual:
        for isp in isps:
            log(f"[{isp}] {addr.one_line()}")
            res = scrape_one_isp(cfg, addr, isp, headless=headless, manual=manual, log=log)
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
                 limit: int | None = None, log=print, progress=None) -> Path:
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
        if isp not in SCRAPERS:
            log(f"skip unknown ISP {isp}")
            continue
        with browser_context(cfg, isp, headless) as ctx:
            for t in group:
                addr = Address(line1=t["address_primary"], city=t["city"], state=t["state"],
                               zip=t["zip"], location_id=t["location_id"])
                log(f"[{isp}] {t['grp']} {t['location_id']} {addr.one_line()}")
                res = _scrape_one(ctx, cfg, isp, addr, manual, log)
                log(f"   -> {res.status}: {', '.join(u['upi'] for u in res.upis) or '-'} {res.message}")
                append_rows(out, res.rows(extra={k: t.get(k, "") for k in
                                                 ("grp", "block_geoid", "nbm_frns", "nbm_brand", "nbm_technologies")}))
                n_done += 1
                if progress:
                    progress(n_done, len(todo), res)
                time.sleep(random.uniform(lo, hi))
    return out
