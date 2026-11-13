import re

import pandas as pd

from clscraper.bq import render, table_names
from clscraper.definitions import match_isp, qualifies
from clscraper.pipeline import build_tasks
from clscraper.scrapers.base import Address
from clscraper.upi import find_upis, find_upis_in_many

CFG = {
    "bigquery": {"nbm_table": "p.d.nbm", "fabric_table": "p.d.fab", "output_dataset": "p.out"},
    "competition": {"definition": "wired"},
    "speed": {"min_down": 100, "min_up": 20},
    "scraper": {"isps": ["att", "verizon", "spectrum", "xfinity"]},
}


def test_upi_in_visible_text():
    hits = find_upis("Unique Plan Identifier F0001234567ABC123 Monthly price $50")
    assert [h["upi"] for h in hits] == ["F0001234567ABC123"]
    assert hits[0]["frn"] == "0001234567" and hits[0]["plan_code"] == "ABC123"


def test_upi_in_html_attribute_and_json():
    html = '<div data-upi="F0009876543XYZ9"></div>'
    js = '{"uniquePlanIdentifier":"F0005555555PLAN01"}'
    got = {h["upi"] for h in find_upis_in_many([("html", html), ("network", js)])}
    assert got == {"F0009876543XYZ9", "F0005555555PLAN01"}


def test_upi_with_separators_next_to_caption():
    hits = find_upis("<td>Unique Plan Identifier:</td><td>F 0001234567-INT300</td>")
    assert "F0001234567INT300" in {h["upi"] for h in hits}


def test_no_false_positive_inside_longer_token():
    assert find_upis("ABCF0001234567XYZ and F123") == []


def test_address_parse():
    a = Address.parse("123 Main St Apt 4, Durham, NC 27701")
    assert (a.line1, a.unit, a.city, a.state, a.zip) == ("123 Main St", "Apt 4", "Durham", "NC", "27701")


def test_isp_matching():
    assert match_isp("AT&T Inc.") == "att"
    assert match_isp("Spectrum") == "spectrum"
    assert match_isp("Comcast Cable Communications") == "xfinity"
    assert match_isp("Cox Communications") == "spectrum"
    assert match_isp("Frontier Communications") == "verizon"
    assert match_isp("Starlink") is None


def test_qualifies_wired():
    assert qualifies({"technology": 50, "down": 1000, "up": 1000}, "wired", 100, 20)
    assert not qualifies({"technology": 71, "down": 300, "up": 50}, "wired", 100, 20)
    assert not qualifies({"technology": 40, "down": 100, "up": 10}, "wired", 100, 20)


def test_sql_templates_render():
    t = table_names(CFG)
    for f, extra in [("01_block_competition.sql", {}), ("02_select_blocks.sql", {"definition": "wired"}),
                     ("03_sample_locations.sql", {}), ("lookup_location.sql", {}), ("lookup_providers.sql", {})]:
        sql = render(f, **t, **extra)
        assert not re.search(r"\{\w+\}", sql), f


def test_build_tasks():
    locs = pd.DataFrame([{
        "grp": "most_competitive", "block_geoid": "371830001001000", "location_id": "1",
        "address_primary": "1 A St", "city": "Raleigh", "state": "NC", "zip": "27601",
        "providers_json": '[{"brand_name":"AT&T","frn":"0005193701","technology":50,"down":1000,"up":1000},'
                          '{"brand_name":"Spectrum","frn":"0001111111","technology":40,"down":1000,"up":35},'
                          '{"brand_name":"Starlink","frn":"0002222222","technology":61,"down":200,"up":20},'
                          '{"brand_name":"Local Fiber Co","frn":"0003333333","technology":50,"down":500,"up":500}]',
    }])
    tasks = build_tasks(locs, CFG)
    assert sorted(tasks["isp"]) == ["att", "search_local_fiber_co", "spectrum"]      # satellite (61) excluded
    assert dict(zip(tasks["isp"], tasks["check_mode"]))["search_local_fiber_co"] == "search"


def test_partition_filter_is_literal():
    from clscraper.bq import nbm_partition_filter
    cfg = {"bigquery": {"nbm_period": "2025_12"}, "scope": {"states": ["NC"]}}
    f = nbm_partition_filter(cfg)
    assert "n.period = '2025_12'" in f and "'North Carolina'" in f and "'North_Carolina'" in f
    assert nbm_partition_filter({"bigquery": {}, "scope": {"states": ["NC"]}}) == ""


LABEL = """Broadband Facts
Fios Home Internet
Fiber 300 Mbps
Fixed Broadband Consumer Disclosure
Monthly Price $49.99
This Monthly Price is an introductory rate. This rate applies for 24 months.
After 24 months, the monthly price will be $69.99.
Length of Contract 24 months
Additional Charges & Terms
Provider Monthly Fees
Router rental $15.00
One-time Fees at the Time of Purchase
Installation Fee $99.00
Early Termination Fee $0
Government Taxes Varies by Location
Speeds Provided with Plan
Typical Download Speed 1.2 Gbps
Typical Upload Speed 880 Mbps
Typical Latency 7 ms
Data Included with Monthly Price Unlimited
Unique Plan Identifier F0001234567FIOS300"""


def test_label_parser_fields():
    from clscraper.label_parser import enrich_upis
    hits = enrich_upis(find_upis(LABEL, "dom_text"), [("dom_text", LABEL)])
    h = hits[0]
    assert h["monthly_price"] == "49.99"
    assert h["intro_months"] == "24" and h["price_after_intro"] == "69.99"
    assert h["contract"].startswith("24 month")
    assert h["typical_download_mbps"] == "1200" and h["typical_upload_mbps"] == "880"
    assert h["typical_latency_ms"] == "7" and h["data_included"].lower() == "unlimited"
    assert "Fiber 300" in h["plan_name"]
    assert "Router rental" in h["provider_monthly_fees"]
    assert h["early_termination_fee"] == "$0"


def test_label_parser_two_labels_do_not_mix():
    from clscraper.label_parser import enrich_upis
    two = LABEL + "\n" + LABEL.replace("49.99", "79.99").replace("FIOS300", "FIOS1G").replace("Fiber 300 Mbps", "Fiber 1 Gig")
    hits = {h["upi"]: h for h in enrich_upis(find_upis(two, "dom_text"), [("dom_text", two)])}
    assert hits["F0001234567FIOS300"]["monthly_price"] == "49.99"
    assert hits["F0001234567FIOS1G"]["monthly_price"] == "79.99"


def test_label_parser_json():
    from clscraper.label_parser import enrich_upis
    js = '{"planName":"Gig","monthlyPrice":"80.00","typicalDownloadSpeed":"940","typicalLatency":"9","uniquePlanIdentifier":"F0001234567GIG1"}'
    h = enrich_upis(find_upis(js, "network"), [("network:x", js)])[0]
    assert h["monthly_price"] == "80.00" and h["typical_download_mbps"] == "940" and h["typical_latency_ms"] == "9"


def test_address_matching():
    from clscraper.address_match import house_number, normalise_street, rank_candidates
    assert normalise_street("100 North Main Street, Apt. 4") == "100 N MAIN ST APT 4"
    assert house_number("100A Main St") == "100A"
    c = rank_candidates("100 Main Street", [{"address_primary": "100 MAPLE AVE"}, {"address_primary": "100 MAIN ST"}])
    assert c[0]["address_primary"] == "100 MAIN ST"


def test_unit_formatting():
    a = Address("3147 Broadway", "New York", "NY", "10027", unit="17")
    assert a.one_line() == "3147 Broadway Apt 17, New York, NY 10027"
    assert a.one_line(include_unit=False) == "3147 Broadway, New York, NY 10027"
    assert Address("1 A St", unit="Unit 4B").street() == "1 A St Unit 4B"


ATT_LABEL = ("Broadband Facts AT&T AT&T Internet Air Fixed Broadband Consumer Disclosure Monthly Price $65.00* "
             "Additional Charges & Terms Provider Monthly Fees One-time Fees at the Time of Purchase Late Payment Fee $9.99 "
             "Early Termination Fee $0.00 Speeds Provided with Plan Typical Download Speed 89-346 Mbps "
             "Typical Upload Speed 8-30 Mbps Typical Latency 40-69 Milliseconds Data Included with Monthly Price Unlimited "
             "fcc.gov/consumers F0005937974804842809955155 .gpcoverlayvisibility{opacity: 0}")


def test_label_parser_ranges_from_real_att_label():
    from clscraper.label_parser import enrich_upis
    h = enrich_upis(find_upis(ATT_LABEL, "dom_text"), [("dom_text", ATT_LABEL)])[0]
    assert h["typical_download_mbps"] == "89-346" and h["typical_upload_mbps"] == "8-30"
    assert h["typical_latency_ms"] == "40-69" and h["monthly_price"] == "65.00"
    assert h["early_termination_fee"] == "$0.00" and "Late Payment Fee $9.99" in h["one_time_fees"]
    assert "gpcoverlay" not in h["label_text"]



def test_resolve_every_provider():
    from clscraper.scrapers import SCRAPERS, resolve_provider
    assert resolve_provider("AT&T") == ("att", "tuned")
    assert resolve_provider("Frontier Communications") == ("verizon", "tuned")
    assert resolve_provider("T-Mobile") == ("tmobile", "known")
    key, kind = resolve_provider("Morgan Wireless LLC")
    assert kind == "search" and key == "search_morgan_wireless_llc"
    assert SCRAPERS[key].assisted_only and "duckduckgo.com" in SCRAPERS[key].start_url


def test_every_module_imports():
    """Catches syntax/indentation errors in modules the other tests don't touch."""
    import importlib
    import pkgutil

    import clscraper
    for m in pkgutil.walk_packages(clscraper.__path__, "clscraper."):
        importlib.import_module(m.name)
    import py_compile
    py_compile.compile("app.py", doraise=True)


def test_import_saved_pages_html_and_mhtml():
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    from clscraper.importer import file_to_chunks, import_labels
    html = "<html><body><div class='modal'>" + ATT_LABEL.replace(" Monthly", "<br>Monthly") + "</div></body></html>"
    r = import_labels("att", Address("13308 Ballfield Rd NE", "Little Orleans", "MD", "21766"),
                      file_to_chunks("plans.html", html.encode()))
    assert r.status == "ok" and r.upis[0]["upi"] == "F0005937974804842809955155"
    assert r.upis[0]["typical_download_mbps"] == "89-346"
    m = MIMEMultipart("related"); m["From"] = "<Saved by Blink>"
    m.attach(MIMEText(html, "html", "utf-8"))          # Chrome .mhtml uses quoted-printable/base64 parts
    chunks = file_to_chunks("plans.mhtml", m.as_bytes())
    assert import_labels("att", Address("x"), chunks).upis[0]["monthly_price"] == "65.00"
