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


def test_label_parser_ignores_stray_punctuation():
    from clscraper.label_parser import parse_label
    assert parse_label('Typical Download Speed . Mbps') == {}
    assert parse_label('typicalDownloadSpeed":","x') == {}
    assert parse_label("Typical Download Speed 300 to 500 Mbps")["typical_download_mbps"] == "300-500"
    assert parse_label("Typical Latency 1.5 ms Typical Upload Speed 1.2 Gbps") == {
        "typical_latency_ms": "1.5", "typical_upload_mbps": "1200"}


# ---------------------------------------------------------------- plan files (machine-readable labels)
FCC_SPEC_CSV = """connection_type,fcc_registration_number,unique_plan_identifier,network_technology_type,provider_name,service_plan_name,bandwidth_download_units,bandwidth_download_marketed_high,bandwidth_upload_units,bandwidth_upload_marketed_high,bandwidth_download_typical_low,bandwidth_download_typical_high,price_type,price_recurring,fee_introductory,fee_introductory_description,contract_required
FIXED,0003768165,F0003768165AAA1,40,Xfinity,1 Gig,Mbps,1000,Mbps,35,1100,1100,Post-Paid,110.0,60.0,Introductory price for 12 months,No
FIXED,0003768165,F0003768165AAA2,40,Xfinity,1 Gig,Mbps,1000,Mbps,100,1100,1100,Post-Paid,110.0,60.0,Introductory price for 12 months,No
FIXED,0003768165,F0003768165AAA3,40,Xfinity,1 Gig,Mbps,1000,Mbps,1000,1100,1100,Post-Paid,110.0,60.0,Introductory price for 12 months,No
FIXED,0003768165,F0003768165AAA4,50,Xfinity,2 Gig,Gbps,2,Gbps,2,2.1,2.2,Post-Paid,140.0,,,No
FIXED,0003768165,F0003768165AAA5,40,Xfinity,Internet Essentials,Mbps,75,Mbps,10,80,80,Post-Paid,14.95,,,No
MOBILE,0003768165,M0003768165AAA6,500,Xfinity,Unlimited,Mbps,100,Mbps,10,,,Post-Paid,40,,,No
"""
ATT_CSV = '''"FCCPlanId","providerName","planName","serviceType","priceAmount","introductoryRateValue","priceAfterIntroductory","introductoryPeriodLength","contractStatus","terminationFee","onTimeyFeeType","oneTimeFee","monthlyFeeType","monthlyFee","speedsDownloadSpeed","speedsUploadSpeed","speedsLatency","dataMonthly","customerSupportlinkText","discountsBundlesLinks"
"F0005937974418090127942941","AT&T","Internet 300 (Fiber 300Mbps)","Fixed","65.00*","is not","","","does not require a contract.","0.00","Installation","0.00","","","397.9 Mbps","377.7 Mbps","10","Unlimited","att.com/support",""
"F0005937974970478562076235","AT&T","Internet 5000 (Fiber 5GIG)","Fixed","255.00*","is not","","","does not require a contract.","0.00","Installation","0.00","","","5115.4 Mbps","5315.0 Mbps","9","Unlimited","att.com/support",""
"F0005937974254725150573087","AT&T","Internet 100 (Copper 100Mbps)","Fixed","70.00*","is not","","","does not require a contract.","0.00","Installation","99.00","","","107.0 Mbps","20.6 Mbps","40","Unlimited","att.com/support",""
"F0005937974000000000000001","AT&T","AT&T Internet Air","Fixed","65.00*","is not","","","does not require a contract.","0.00","","","","","75-225 Mbps","10-30 Mbps","40-69","Unlimited","att.com/support",""
"F0005937974984698571725734","AT&T","Business Fiber 300S","Fixed","70*","is not","","","does not require a contract.","0.00","Installation","150.00","","","300 Mbps","300 Mbps","10","Unlimited","business.att.com/support","att.com/smbbbdiscounts"
"F0005937974213031549491373","AT&T","AT&T Internet 300 (eRate)","Fixed","70","is not","","","","0.00","","","","","300 Mbps","300 Mbps","10","Unlimited","",""
'''


def _catalogs():
    import io

    from clscraper.catalog import normalise
    return {"xfinity": normalise(pd.read_csv(io.StringIO(FCC_SPEC_CSV), dtype=str), "xfinity", "x.csv"),
            "att": normalise(pd.read_csv(io.StringIO(ATT_CSV), dtype=str), "att", "a.csv")}


def test_plan_file_parsing():
    cats = _catalogs()
    x = cats["xfinity"].set_index("upi")
    assert x.loc["F0003768165AAA4", "down_mbps"] == 2000 and x.loc["F0003768165AAA4", "technology"] == 50
    assert x.loc["F0003768165AAA5", "segment"] == "low-income program"
    a = cats["att"].set_index("plan_name")
    assert a.loc["Internet 5000 (Fiber 5GIG)", "down_mbps"] == 5000
    assert a.loc["Internet 100 (Copper 100Mbps)", "technology"] == 10
    assert a.loc["AT&T Internet Air", "technology"] == 71 and a.loc["AT&T Internet Air", "down_mbps"] == 75
    assert a.loc["Business Fiber 300S", "segment"] == "business"
    assert a.loc["AT&T Internet 300 (eRate)", "segment"].startswith("E-Rate")
    assert a.loc["Internet 300 (Fiber 300Mbps)", "monthly_price"] == 65


def test_plan_matching_uses_frn_technology_and_speed():
    from clscraper.catalog import match_location, summarise
    provs = [
        {"brand_name": "AT&T", "frn": "0005937974", "technology": 50, "down": 1000, "up": 1000},
        {"brand_name": "AT&T", "frn": "0005937974", "technology": 71, "down": 100, "up": 20},
        {"brand_name": "Comcast", "frn": "0003768165", "technology": 40, "down": 1200, "up": 100},
        {"brand_name": "Verizon", "frn": "0018506568", "technology": 50, "down": 2300, "up": 2300},
        {"brand_name": "Starlink", "frn": "0000000001", "technology": 61, "down": 350, "up": 40},
    ]
    plans, cov = match_location(provs, _catalogs())
    fiber = set(plans[(plans.isp == "att") & (plans.nbm_technology == 50)]["plan_name"])
    assert fiber == {"Internet 300 (Fiber 300Mbps)"}               # 5 Gig too fast; business/eRate excluded
    assert set(plans[plans.nbm_technology == 71]["plan_name"]) == {"AT&T Internet Air"}
    xf = plans[plans.isp == "xfinity"]
    assert list(xf["upi"]) == ["F0003768165AAA2"]                   # 100-up variant; fiber + Essentials excluded
    assert cov.set_index("provider").loc["Verizon", "status"] == "no plan file for this ISP"
    assert "Starlink" not in set(cov["provider"])                   # satellite skipped
    assert (cov.set_index("provider").loc["Comcast", "matched_by"]) == "FRN"
    assert len(summarise(plans)) == 3


def test_labels_become_a_reusable_plan_file(tmp_path, monkeypatch):
    from clscraper import catalog
    monkeypatch.setattr(catalog, "CATALOG_DIR", tmp_path)
    assert catalog.tech_from_name("Fios 1 Gig") == 50 and catalog.tech_from_name("LTE Home Plus") == 71
    assert catalog.speed_from_name("Fios 1 Gig") == 1000 and catalog.speed_from_name("Internet 300 Mbps") == 300
    n, path = catalog.add_labels("att_labels", [("import:page", ATT_LABEL)], technology=71, provider_name="AT&T")
    assert n == 1 and path.name == "labels.csv"
    cat = catalog.load("att_labels")
    row = cat.iloc[0]
    assert row["upi"] == "F0005937974804842809955155" and row["technology"] == 71 and row["monthly_price"] == 65
    assert row["down_mbps"] == 89                      # low end of the typical 89-346 range
    # adding the same page again does not duplicate
    assert catalog.add_labels("att_labels", [("import:page", ATT_LABEL)], 71, "AT&T")[0] == 1
    plans, cov = catalog.match_location(
        [{"brand_name": "AT&T", "frn": "0005937974", "technology": 71, "down": 300, "up": 50}],
        {"att_labels": cat})
    assert len(plans) == 1 and cov.iloc[0]["matched_by"] == "FRN"


def test_coverage_report_and_match_many():
    from clscraper.catalog import coverage_report, match_many
    provs = pd.DataFrame([{"frn": "0003768165", "brand_name": "Xfinity", "technologies": "40", "n_locations": 10},
                          {"frn": "0026963629", "brand_name": "Morgan Wireless LLC", "technologies": "70",
                           "n_locations": 2}])
    rep = coverage_report(provs, _catalogs()).set_index("brand_name")
    assert rep.loc["Xfinity", "plan_file"] == "xfinity" and rep.loc["Morgan Wireless LLC", "plan_file"] == ""
    assert rep.loc["Morgan Wireless LLC", "suggested_key"] == "morgan_wireless_llc"
    import json
    locs = pd.DataFrame([{"grp": "most", "location_id": "1", "providers_json": json.dumps(
        [{"brand_name": "Comcast", "frn": "0003768165", "technology": 40, "down": 1200, "up": 100}])}])
    plans, cov = match_many(locs, _catalogs())
    assert list(plans["upi"]) == ["F0003768165AAA2"] and plans.columns[0] == "location_id"


def test_fcc_template_csv_one_file_per_plan():
    import io

    from clscraper.catalog import normalise
    csv = ("unique_plan_id,provider_name,service_plan_name,tier_plan_name,connection_type,monthly_price ,intro_rate,"
           "intro_rate_price,intro_rate_time,contract_req,typical_download_speed ,typical_upload_speed ,typical_latency\n"
           "F0002154367ATCMD20241G0012,Antietam Broadband ,1 Gig,NULL,Fixed,90,Yes,70,60 months,No,1323.4,47.82,6\n")
    df = normalise(pd.read_csv(io.StringIO(csv), dtype=str), "antietam",
                   "https://x.com/label/2026/wc/Promo/Docsis/1-Gig.csv")
    r = df.iloc[0]
    assert r["frn"] == "0002154367" and r["technology"] == 40          # 'Docsis' in the file path
    assert r["down_mbps"] == 1000 and r["monthly_price"] == 90 and r["intro_price"] == 70
    assert r["segment"] == "residential"
