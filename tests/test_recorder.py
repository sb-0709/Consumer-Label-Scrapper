"""End to end: record an ISP's address check once on a fake ISP site, then replay it automatically."""
from pathlib import Path

import pytest

FAKE = Path(__file__).parent / "fake_isp" / "index.html"


def _chrome_available() -> bool:
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            pw.chromium.launch(headless=True, channel="chrome").close()
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _chrome_available(), reason="needs Playwright + Chrome")
def test_record_once_then_check_any_address(tmp_path, monkeypatch):
    from clscraper import recorder
    from clscraper.scrapers import Address, isps
    from clscraper.scrapers.runner import scrape_one_isp

    monkeypatch.setattr(recorder, "SITES_FILE", tmp_path / "isp_sites.yaml")
    monkeypatch.setattr(isps, "SITES_FILE", tmp_path / "isp_sites.yaml")

    def drive(page):                                  # the clicks a person makes during setup
        page.wait_for_timeout(800)
        page.click("#open"); page.wait_for_timeout(600)
        page.click("#street-box"); page.wait_for_timeout(600)
        page.type("#street-box", "10 Main St", delay=20); page.wait_for_timeout(400)
        page.click(".addr-suggestions li >> nth=0"); page.wait_for_timeout(600)
        page.click(".go-btn"); page.wait_for_load_state("domcontentloaded"); page.wait_for_timeout(1500)
        page.click("#bf"); page.wait_for_timeout(1500)

    site = recorder.record("fake_fiber", "Fake Fiber Co", FAKE.as_uri(), "10 Main St", frns=["0012345678"],
                           _drive=drive, _headless=True, timeout_minutes=0.5, log=lambda *_: None)
    assert site["address_input"][0] == "#street-box" and site["suggestion"] == ["ul.addr-suggestions li"]
    assert isps.resolve_provider("Unrelated name", "0012345678") == ("fake_fiber", "tuned")

    cfg = {"scraper": {"headless": True, "timeout_ms": 15000, "save_artifacts": False, "trace": False,
                       "browser_channel": "chrome", "output_dir": str(tmp_path)}}
    r = scrape_one_isp(cfg, Address("22 Oak Ave", "Towson", "MD", "21204"), "fake_fiber",
                       headless=True, log=lambda *_: None)
    assert r.status == "ok"
    assert r.upis[0]["upi"] == "F0012345678FAKE500" and r.upis[0]["monthly_price"] == "55.00"
    isps.SCRAPERS.pop("fake_fiber", None); isps.TUNED.discard("fake_fiber")
