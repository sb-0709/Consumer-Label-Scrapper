"""Per-ISP settings. Only URLs + selectors differ; the engine lives in base.py.

!! These selectors are STARTING POINTS. ISP sites are redesigned often and A/B tested.
!! Before a real run, open each URL with `playwright codegen <url>`, enter a test
!! address, and paste the selectors it records at the FRONT of the lists below.
"""
from __future__ import annotations

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


class ATTScraper(BaseISPScraper):
    key, name = "att", "AT&T"
    start_url = "https://www.att.com/internet/"
    open_form_selectors = ['button:has-text("Check availability")', 'a:has-text("Check availability")',
                           'button:has-text("Check your address")', *BaseISPScraper.open_form_selectors]
    label_link_patterns = [r"broadband\s*facts", r"view\s*broadband\s*facts", *BaseISPScraper.label_link_patterns]


class VerizonScraper(BaseISPScraper):
    key, name = "verizon", "Verizon (Fios / 5G Home)"
    start_url = "https://www.verizon.com/home/internet/"
    open_form_selectors = ['button:has-text("Check availability")', 'a:has-text("Check availability")',
                           'button:has-text("Get started")', *BaseISPScraper.open_form_selectors]


class XfinityScraper(BaseISPScraper):
    key, name = "xfinity", "Xfinity (Comcast)"
    start_url = "https://www.xfinity.com/learn/offers"
    open_form_selectors = ['button:has-text("Enter address")', 'button:has-text("Check availability")',
                           'a:has-text("Shop offers")', *BaseISPScraper.open_form_selectors]
    submit_selectors = ['button:has-text("See offers")', 'button:has-text("View offers")',
                        *BaseISPScraper.submit_selectors]


class FrontierScraper(BaseISPScraper):
    key, name = "frontier", "Frontier"
    start_url = "https://frontier.com/shop/internet"
    open_form_selectors = ['button:has-text("Check availability")', 'a:has-text("Check availability")',
                           'button:has-text("Shop now")', *BaseISPScraper.open_form_selectors]


class CoxScraper(BaseISPScraper):
    key, name = "cox", "Cox"
    start_url = "https://www.cox.com/residential/internet.html"
    open_form_selectors = ['button:has-text("Check availability")', 'a:has-text("Check availability")',
                           'a:has-text("Shop Cox Internet")', *BaseISPScraper.open_form_selectors]


SCRAPERS: dict[str, type[BaseISPScraper]] = {
    cls.key: cls for cls in (ATTScraper, VerizonScraper, SpectrumScraper,
                             XfinityScraper, FrontierScraper, CoxScraper)
}
