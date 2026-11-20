"""Broadband plan finder (Streamlit):  streamlit run app.py

Fully automatic, no ISP website is opened per address:
1. Type an address  -> matched to the FCC Fabric; the National Broadband Map (NBM) lists the ISPs there
2. Plans            -> each ISP's plan file (its machine-readable Broadband Facts labels, collected
                       once per ISP), filtered to the technology + max speed the NBM lists at the address
The "ISP plan files" tab shows which ISPs still need a plan file and lets you add one.
"""
from __future__ import annotations

import io

import pandas as pd
import streamlit as st

from clscraper import catalog
from clscraper.config import load_config, output_dir
from clscraper.definitions import TECH_NAMES
from clscraper.pipeline import index_states

st.set_page_config(page_title="Broadband Plan Finder", page_icon="📡", layout="wide")
cfg = load_config()
OUT = output_dir(cfg)
ss = st.session_state
STATES = [s.upper() for s in index_states(cfg)]

PLAN_COLUMNS = {
    "plan_name": "Plan", "down_mbps": "Down (Mbps)", "up_mbps": "Up (Mbps)",
    "monthly_price": "Monthly price ($)", "intro_price": "Intro price ($)", "intro_period": "Intro period",
    "typical_down_mbps": "Typical down", "typical_up_mbps": "Typical up", "latency_ms": "Latency (ms)",
    "contract": "Contract", "data_included": "Data", "one_time_fees": "One-time fees",
    "upi": "Unique Plan Identifier", "n_upis": "# UPIs (price variants)", "technology_check": "Technology",
}


WEB_COLUMNS = {
    "plan_name": "Plan", "monthly_price": "Monthly price ($)", "intro_months": "Intro (months)",
    "price_after_intro": "Price after intro ($)", "typical_download_mbps": "Typical down (Mbps)",
    "typical_upload_mbps": "Typical up (Mbps)", "typical_latency_ms": "Latency (ms)", "contract": "Contract",
    "data_included": "Data", "one_time_fees": "One-time fees", "upi": "Unique Plan Identifier",
}
STATUS_TEXT = {
    "ok": ("✅", "Plans found on the website"),
    "no_upi_found": ("⚠️", "Page loaded, but no Broadband Facts labels were found"),
    "not_serviceable": ("⛔", "The website says it does not serve this address"),
    "address_issue": ("❓", "The website could not match the address"),
    "blocked": ("🛑", "The website's bot protection refused the automated check"),
    "needs_assisted": ("➖", "No automatic website check for this provider yet"),
    "call_to_order": ("📞", "The website shows no online plans here (asks you to call)"),
    "timeout": ("⏱️", "Timed out"),
    "error": ("❌", "The website check failed"),
}
SITE_NAMES = {"att": "AT&T", "verizon": "Verizon", "spectrum": "Spectrum", "xfinity": "Xfinity"}


@st.cache_data(show_spinner="Reading ISP plan files ...")
def load_catalogs(files: tuple) -> dict:
    """Cached on the (isp, files, date) triples, so a new or updated file is picked up automatically."""
    return catalog.load_all([k for k, f, _ in files if f])


def current_catalogs() -> dict:
    s = catalog.status()
    return load_catalogs(tuple(zip(s["isp"], s["file"], s["downloaded"])))


# ---------------------------------------------------------------- sidebar
with st.sidebar:
    st.header("Plan files")
    st.caption("Each ISP's machine-readable Broadband Facts labels (all its current plans), "
               "collected once per ISP and reused for every address.")
    if "catalog_errors" not in ss:                       # once per session: fetch missing / stale files
        with st.spinner("Updating ISP plan files ..."):
            ss.catalog_errors = catalog.ensure_fresh(log=lambda *_: None)
    cat_status = catalog.status()
    have = cat_status[cat_status["file"] != ""]
    for _, r in have.iterrows():
        st.caption(f"✅ **{r['name']}** · {r['downloaded']}" + (" · auto-updated" if r["auto_download"] else ""))
    st.caption(f"{len(have)} ISP(s) with a plan file. See the **ISP plan files** tab to add more.")
    for k, err in (ss.catalog_errors or {}).items():
        st.caption(f"⚠️ {k}: {err[:160]}")
    if st.button("Download plan files again"):
        ss.catalog_errors = {}
        for k in cat_status[cat_status["auto_download"]]["isp"]:
            try:
                catalog.download(k, log=lambda *_: None)
            except Exception as e:  # noqa: BLE001
                ss.catalog_errors[k] = str(e)
        st.rerun()
    st.divider()
    from clscraper.pipeline import location_source
    _table, _period = location_source(cfg)
    if _table:
        st.caption(f"Locations + providers: `{_table}` · NBM period {_period}")
    else:
        st.caption(f"NBM `{cfg['bigquery']['nbm_table']}` · period {_period}")
        st.caption(f"Fabric `{cfg['bigquery']['fabric_table']}`")
    st.caption(f"Address lookup covers: {', '.join(STATES) or 'all states'}")

st.title("📡 Broadband Plan Finder")
st.write("Enter an address to see every internet plan available there: the providers come from the "
         "FCC National Broadband Map, the plans from each provider's FCC **Broadband Facts** labels.")

tab_find, tab_files, tab_sample = st.tabs(["🔎 Find plans", "🛠️ ISP setup", "🧪 Research sample"])

# ================================================================ FIND PLANS
with tab_find:
    with st.form("address"):
        c1, c2 = st.columns([4, 1])
        street = c1.text_input("Street address", placeholder="100 Main St")
        unit = c2.text_input("Apt / unit")
        c3, c4, c5 = st.columns([3, 1, 1])
        city = c3.text_input("City")
        state = c4.text_input("State", value=(STATES or ["MD"])[0], max_chars=2)
        zip_ = c5.text_input("ZIP", max_chars=5)
        go = st.form_submit_button("Find plans", type="primary")

    if go:
        if not street or len(zip_) != 5:
            st.error("Please enter a street address and a 5-digit ZIP code.")
        else:
            ss.addr_input = dict(street=street, unit=unit, city=city, state=state.upper(), zip=zip_)
            ss.candidates, ss.index_error = [], None
            try:
                from clscraper.pipeline import search_address
                with st.spinner("Looking up the address in the FCC Fabric ..."):
                    ss.candidates = search_address(cfg, street, zip_, log=lambda *_: None)
            except Exception as e:  # index missing, no credentials, ...
                ss.index_error = f"{type(e).__name__}: {e}"

    if "addr_input" in ss:
        a = ss.addr_input
        cands = ss.get("candidates") or []
        loc = None
        st.subheader("1 · Location")
        if ss.get("index_error"):
            st.error("Couldn't search the address table. Run `cls build-index` once.\n\n" + ss.index_error[:300])
        elif not cands and STATES and a["state"] not in STATES:
            st.warning(f"The address lookup only covers **{', '.join(STATES)}**. To cover {a['state']} (or every "
                       f"state), add it to `app.index_states` in config.yaml (`[]` = all states) and run "
                       f"`cls build-index` once.")
        elif not cands:
            st.info("No matching location in the FCC Fabric for this street and ZIP. Check the spelling, or try "
                    "without the apartment number.")
        else:
            labels = [f"{c['address_primary']}, {c['city']} {c['zip']}  ·  match {c['match_score']:.0%}"
                      for c in cands]
            pick = st.selectbox("Matched Fabric location (best match first)", range(len(cands)),
                                format_func=lambda i: labels[i])
            loc = cands[pick]
            m1, m2, m3 = st.columns(3)
            m1.metric("Fabric location_id", loc["location_id"])
            m2.metric("Census block", loc["block_geoid"])
            m3.metric("Units at address", int(loc["unit_count"] or 1))

        if loc:
            cats = current_catalogs()
            st.subheader("2 · Providers at this address (FCC National Broadband Map)")
            all_prov = pd.DataFrame(loc["providers"]) if loc["providers"] else pd.DataFrame()
            prov = all_prov[~all_prov["technology"].isin([60, 61])] if not all_prov.empty else all_prov
            if not all_prov.empty and len(prov) < len(all_prov):
                st.caption(f"Satellite providers (GSO 60 / NGSO-LEO 61) hidden: "
                           f"{', '.join(sorted(set(all_prov[all_prov['technology'].isin([60, 61])]['brand_name'])))}")
            if prov.empty:
                st.info("The map lists no terrestrial (non-satellite) providers at this location.")
            else:
                min_down, min_up = float(cfg["speed"]["min_down"]), float(cfg["speed"]["min_up"])
                prov = prov.copy()
                prov[f"Meets {min_down:g}/{min_up:g}"] = [
                    "✅" if d >= min_down and u >= min_up else "—" for d, u in zip(prov["down"], prov["up"])]
                prov["Technology"] = prov["technology"].map(TECH_NAMES)
                st.dataframe(prov.rename(columns={"brand_name": "Provider", "down": "Max down (Mbps)",
                                                  "up": "Max up (Mbps)", "frn": "FRN"})[
                    ["Provider", "Technology", "Max down (Mbps)", "Max up (Mbps)",
                     f"Meets {min_down:g}/{min_up:g}", "FRN"]], hide_index=True, width="stretch")

            # ------------------------------------------------ 3 · each provider's website, this address
            st.subheader("3 · Plans on each provider's website for this address")
            from clscraper.scrapers import TUNED, Address
            from clscraper.scrapers.isps import resolve_provider
            site_keys: dict[str, dict] = {}
            for p in (prov.to_dict("records") if not prov.empty else []):
                k = resolve_provider(p["brand_name"], p.get("frn"))[0]
                e = site_keys.setdefault(k, {"brands": [], "frns": set()})
                e["brands"].append(p["brand_name"]); e["frns"].add(p["frn"])
            checkable = [k for k in site_keys if k in TUNED]
            unsupported = [b for k, e in site_keys.items() if k not in TUNED for b in dict.fromkeys(e["brands"])]
            st.caption("The app opens each provider's website, enters this address and reads the Broadband Facts "
                       "labels it shows, so prices and plans are specific to this address. Results are saved to "
                       "`outputs/lookup_results.csv` with the location and census block.")
            if unsupported:
                st.caption(f"No website check set up yet for: {', '.join(dict.fromkeys(unsupported))}. "
                           "Set it up once in the **ISP setup** tab.")
            web = ss.setdefault("web_results", {})
            if st.button("Check provider websites for this address", type="primary", disabled=not checkable):
                from clscraper.scrapers.runner import append_rows, scrape_address
                addr = Address(loc["address_primary"], loc["city"], loc["state"], loc["zip"],
                               unit=a["unit"], location_id=loc["location_id"])
                with st.status(f"Checking {len(checkable)} website(s) for {addr.one_line()} ...",
                               expanded=True) as box:
                    results = scrape_address(
                        cfg, addr, checkable, headless=cfg["scraper"]["headless"], parallel=1, log=lambda *_: None,
                        on_start=lambda k: box.write(f"⏳ {SITE_NAMES.get(k, k)} ..."),
                        on_done=lambda r: box.write(f"{STATUS_TEXT.get(r.status, ('•', ''))[0]} "
                                                    f"{SITE_NAMES.get(r.isp, r.isp)}: {len(r.upis)} plan(s)"))
                    box.update(label="Done", state="complete")
                rows = []
                for r in results:
                    rows += r.rows(extra={"nbm_frns": ";".join(sorted(site_keys[r.isp]["frns"])),
                                          "nbm_brand": ";".join(dict.fromkeys(site_keys[r.isp]["brands"])),
                                          "block_geoid": loc["block_geoid"]})
                append_rows(OUT / "lookup_results.csv", rows)
                web[loc["location_id"]] = (results, rows)
            if loc["location_id"] in web:
                results, rows = web[loc["location_id"]]
                found = pd.DataFrame([r for r in rows if r.get("upi")])
                if not found.empty:
                    st.download_button("⬇️ Plans from the websites for this address (CSV)",
                                       found.assign(city=loc["city"], zip=loc["zip"]).to_csv(index=False),
                                       f"website_plans_{loc['location_id']}.csv", "text/csv")
                for r in results:
                    icon, text = STATUS_TEXT.get(r.status, ("•", r.status))
                    with st.container(border=True):
                        st.markdown(f"**{SITE_NAMES.get(r.isp, r.isp)}** · {icon} {text}"
                                    + (f" — **{len(r.upis)} plan(s)**" if r.upis else ""))
                        if r.upis:
                            df = pd.DataFrame(r.upis)
                            cols = [c for c in WEB_COLUMNS if c in df.columns]
                            st.dataframe(df[cols].rename(columns=WEB_COLUMNS), hide_index=True, width="stretch")
                        if r.message and r.status != "ok":
                            st.caption(r.message[:300])

            # ------------------------------------------------ 4 · published plan files (reference)
            st.subheader("4 · Plans from the providers' published plan files (reference)")
            all_types = st.toggle("Include low-income, prepaid and business plans", value=False)
            plans, coverage = catalog.match_location(loc["providers"], cats, residential_only=not all_types)
            summary = catalog.summarise(plans)
            if summary.empty:
                st.warning("No plans found: none of the providers here has a matching plan file yet.")
            else:
                where = {"location_id": loc["location_id"], "address": loc["address_primary"],
                         "unit": a["unit"], "city": loc["city"], "state": loc["state"], "zip": loc["zip"],
                         "block_geoid": loc["block_geoid"]}
                c1, c2 = st.columns(2)
                c1.download_button("⬇️ Plans (one row per plan tier, CSV)", summary.assign(**where).to_csv(index=False),
                                   f"plans_{loc['location_id']}.csv", "text/csv")
                c2.download_button("⬇️ Every matching Unique Plan Identifier (CSV)",
                                   plans.assign(**where).to_csv(index=False),
                                   f"plan_ids_{loc['location_id']}.csv", "text/csv")
                for (prov_name, tech), grp in summary.groupby(["nbm_provider", "nbm_technology"], sort=False):
                    with st.container(border=True):
                        st.markdown(f"**{prov_name}** · {TECH_NAMES.get(int(tech), tech)} · {len(grp)} plan tier(s)")
                        cols = [c for c in PLAN_COLUMNS if c in grp.columns]
                        st.dataframe(grp[cols].rename(columns=PLAN_COLUMNS), hide_index=True, width="stretch")
                        if (grp["n_upis"] > 1).any():
                            st.caption("Several UPIs per tier = regional price variants in the ISP's file "
                                       "(the file doesn't say which region is which), so prices are shown as a range.")
                        if (grp["speed_rule"] != "download + upload").any():
                            st.caption("No plan fit the map's upload speed, so only download speed was used.")
            if not coverage.empty:
                nofit = coverage[coverage["plans"] == 0]
                missing = nofit[nofit["matched_by"] == ""]["provider"].tolist()
                if missing:
                    st.caption(f"❌ No plan file yet for: **{', '.join(dict.fromkeys(missing))}**. Add one in the "
                               "**ISP plan files** tab and it is used for every address they serve.")
                for _, r in nofit[nofit["matched_by"] != ""].iterrows():
                    st.caption(f"⚠️ {r['provider']} ({TECH_NAMES.get(r['technology'], r['technology'])}, "
                               f"{r['nbm_max_down']:g}/{r['nbm_max_up']:g} Mbps): plan file loaded, but no plan "
                               f"matches this technology and speed.")
            st.caption("These come from each provider's published list of all its plans, matched to this address by "
                       "technology and speed; they are not address-specific prices. How plans are matched: same provider (FRN in the Unique Plan Identifier, or brand name), "
                       "same technology, and plan speed ≤ the max speed the provider reported to the FCC for this "
                       "location. Plan files carry no geography, so these are plans consistent with the FCC map "
                       "at this address.")

# ================================================================ ISP SETUP
with tab_files:
    from clscraper import recorder
    from clscraper.scrapers import TUNED as _TUNED
    from clscraper.scrapers.isps import resolve_provider as _resolve

    st.subheader("Website checks: set up each ISP once")
    st.markdown(
        "To check plans **per address**, the app needs to know how each ISP's address check works. You show it "
        "once: a Chrome window opens on the ISP's site with an instruction bar on top. Do one address lookup as a "
        "customer would (click the address box, type the address it gives you, pick it from the dropdown, submit, "
        "open one *Broadband Facts* label). The app records those steps and then repeats them **automatically for "
        "every address**. It tests the setup right away on other addresses the ISP serves.")
    cov_path = OUT / "provider_coverage.csv"
    sites = recorder.load_all()
    if cov_path.exists():
        provs_all = pd.read_csv(cov_path, dtype={"frn": str})
        provs_all["frn"] = provs_all["frn"].str.zfill(10)

        def site_status(r):
            k, kind = _resolve(r["brand_name"], r["frn"])
            if k in sites:
                t = (sites[k].get("last_test") or {}).get("results", [])
                ok = sum(x["status"] == "ok" for x in t)
                return f"✅ set up {sites[k].get('configured', '')}" + (f" · last test {ok}/{len(t)} ok" if t else "")
            if k in _TUNED:
                return "✅ built in"
            return "➖ not set up"

        provs_all["Website check"] = [site_status(r) for _, r in provs_all.iterrows()]
        n_set = (provs_all["Website check"] != "➖ not set up").sum()
        st.caption(f"{n_set} of {len(provs_all)} providers have a website check.")
        st.dataframe(provs_all.rename(columns={"brand_name": "Provider", "frn": "FRN",
                                               "n_locations": "Locations served"})[
            ["Provider", "FRN", "Locations served", "Website check"]], hide_index=True, width="stretch", height=260)
        todo = provs_all.reset_index(drop=True)
        pick = st.selectbox("Provider to set up", range(len(todo)),
                            format_func=lambda i: f"{todo.loc[i, 'brand_name']} · FRN {todo.loc[i, 'frn']} · "
                                                  f"{todo.loc[i, 'Website check']}")
        row = todo.loc[pick]
        site_url = st.text_input("Page on the ISP's website where customers enter their address",
                                 value=(sites.get(catalog.slug(row["brand_name"])) or {}).get("start_url", ""),
                                 placeholder="https://www.example-isp.com/check-availability")
        c1, c2 = st.columns(2)
        if c1.button("Set up website check", type="primary", disabled=not site_url.startswith("http")):
            from clscraper.pipeline import sample_addresses_for_frn
            key = catalog.slug(row["brand_name"])
            frns = list(provs_all[provs_all["brand_name"] == row["brand_name"]]["frn"].unique())
            samples = sample_addresses_for_frn(cfg, frns, n=3, log=lambda *_: None)
            if not samples:
                st.error("No address served by this provider found in the address table.")
            else:
                first = samples[0]
                first_line = f"{first['address_primary']}, {first['city']}, {first['state']} {first['zip']}"
                with st.spinner(f"A Chrome window is open on {site_url}. Follow the blue bar at the top, using "
                                f"the address {first_line}. Close the window when it says the setup is recorded."):
                    try:
                        recorder.record(key, row["brand_name"], site_url, first_line, frns=frns,
                                        log=lambda *_: None)
                    except Exception as e:  # noqa: BLE001
                        st.error(str(e))
                        samples = []
                if samples:
                    with st.spinner("Testing the setup automatically on other addresses it serves ..."):
                        res = recorder.test_site(cfg, key, samples[1:] or samples, log=lambda *_: None)
                    st.success(f"{row['brand_name']}: website check saved.")
                    st.dataframe(pd.DataFrame(res), hide_index=True, width="stretch")
        k_existing = _resolve(row["brand_name"], row["frn"])[0]
        if c2.button("Test again", disabled=k_existing not in sites):
            from clscraper.pipeline import sample_addresses_for_frn
            frns = sites[k_existing].get("frns") or [row["frn"]]
            with st.spinner("Testing on 3 addresses it serves ..."):
                res = recorder.test_site(cfg, k_existing, sample_addresses_for_frn(cfg, frns, n=3, log=lambda *_: None),
                                         log=lambda *_: None)
            st.dataframe(pd.DataFrame(res), hide_index=True, width="stretch")
    else:
        st.info("Load the provider list first (button below).")

    st.divider()
    st.subheader("Published plan files (reference)")
    cov_path = OUT / "provider_coverage.csv"
    c1, c2 = st.columns([1, 3])
    if c1.button("Load provider list from BigQuery"):
        from clscraper.pipeline import provider_coverage
        with st.spinner("Listing every provider in the FCC map for the indexed states ..."):
            provider_coverage(cfg, log=lambda *_: None)
    c2.caption(f"Every terrestrial provider in the FCC map for {', '.join(STATES) or 'all states'}, "
               "biggest first. Give each one a plan file once and every address it serves is covered.")
    cov = pd.DataFrame()
    if cov_path.exists():
        cov = catalog.coverage_report(pd.read_csv(cov_path, dtype={"frn": str}), current_catalogs())
        done = cov["plan_file"] != ""
        m1, m2, m3 = st.columns(3)
        m1.metric("Providers in the map", len(cov))
        m2.metric("Providers with a plan file", int(done.sum()))
        m3.metric("Provider-locations covered",
                  f"{cov.loc[done, 'n_locations'].sum() / max(cov['n_locations'].sum(), 1):.0%}")
        techs = [", ".join(TECH_NAMES.get(int(t), t) for t in str(x).split(",") if t.strip().isdigit())
                 for x in cov["technologies"]]
        st.dataframe(cov.assign(technologies=techs).rename(columns={
            "brand_name": "Provider", "frn": "FRN", "technologies": "Technologies",
            "n_locations": "Locations served", "status": "Plan file"})[
            ["Provider", "FRN", "Technologies", "Locations served", "Plan file"]],
            hide_index=True, width="stretch", height=320)

    st.subheader("Add plans for an ISP")
    st.markdown(
        "Find the provider's **broadband labels** page (usually linked in its website footer as *Broadband Facts*, "
        "*Broadband Labels* or *Broadband disclosures*). Then give the app **one** of:\n"
        "- the link to its machine-readable **CSV**, or to the labels page / PDF;\n"
        "- the files: the CSV, a label **PDF**, or the labels page saved from your browser (⌘S);\n"
        "- the label text, pasted.\n\n"
        "This is done **once per ISP**, not per address. The labels become that ISP's plan file and are matched to "
        "every address it serves. Pages added later (e.g. a Fios and a 5G Home page for Verizon) are merged in.")
    missing = cov[cov["plan_file"] == ""].reset_index(drop=True) if not cov.empty else pd.DataFrame()
    options = ["(type a new provider)"] + [f"{r.brand_name} · FRN {r.frn}" for r in missing.itertuples()] \
        + [f"{r.name} · existing plan file '{r.isp}'" for r in cat_status.itertuples()]
    choice = st.selectbox("Provider", options)
    if choice == "(type a new provider)":
        prov_name = st.text_input("Provider name", placeholder="Morgan Wireless")
        key = catalog.slug(prov_name) if prov_name else ""
    elif "existing plan file" in choice:
        key = choice.rsplit("'", 2)[1]
        prov_name = cat_status.set_index("isp").loc[key, "name"]
    else:
        r = missing.iloc[options.index(choice) - 1]
        prov_name, key = r["brand_name"], r["suggested_key"]
    tech = st.selectbox("Technology of these plans", list(catalog.TECH_CHOICES),
                        format_func=catalog.TECH_CHOICES.get,
                        help="Used for plans whose name doesn't say it (names with 'Fios', 'Fiber', '5G Home', "
                             "'DSL' ... are detected automatically).")
    url = st.text_input("Provider website, labels page, CSV or PDF link",
                        help="Give the homepage and the app looks for the broadband-labels page itself, then reads "
                             "every label CSV / PDF linked there. It respects robots.txt and re-checks weekly.")
    files = st.file_uploader("…or files", type=["csv", "pdf", "html", "htm", "mhtml", "mht", "txt"],
                             accept_multiple_files=True)
    pasted = st.text_area("…or paste the label text", height=100)
    if st.button("Add to plan file", type="primary", disabled=not (key and (url.strip() or files or pasted.strip()))):
        added, problems = 0, []
        try:
            if url.strip():
                n, _ = catalog.add_from_url(key, url.strip(), tech, prov_name)
                added += n
                if not n:
                    problems.append("No Unique Plan Identifier found from that link.")
            chunks = []
            for f in files or []:
                data = f.getvalue()
                if f.name.lower().endswith(".csv"):
                    catalog.save_uploaded(key, f.name, data)
                    added += len(pd.read_csv(io.BytesIO(data), dtype=str))
                else:
                    chunks += catalog.file_to_chunks(f.name, data)
            if pasted.strip():
                chunks.append(("import:pasted text", pasted))
            if chunks:
                n, _ = catalog.add_labels(key, chunks, tech, prov_name)
                added += n
                if not n:
                    problems.append("No Unique Plan Identifier found in the files / text. Make sure the labels "
                                    "were open (expanded) when you saved the page.")
        except Exception as e:  # noqa: BLE001
            problems.append(f"{type(e).__name__}: {e}")
        if added:
            st.success(f"{prov_name}: plan file '{key}' now has {added} plan(s). It is used for every address "
                       "this provider serves.")
        for p in problems:
            st.error(p)

# ================================================================ RESEARCH SAMPLE
with tab_sample:
    locs_path = OUT / "sample_locations.csv"
    st.write("Plans for the most / least competitive locations sampled by `cls pipeline`.")
    if not locs_path.exists():
        st.info("No sample yet. Run `cls pipeline` to sample locations from BigQuery.")
    else:
        locs = pd.read_csv(locs_path, dtype=str).fillna("")
        st.write(f"{len(locs)} sampled locations in `{locs_path.name}`")
        all_types_s = st.toggle("Include low-income, prepaid and business plans", value=False, key="sample_types")
        if st.button("Find plans for all sampled locations", type="primary"):
            plans_s, cov_s = catalog.match_many(locs, current_catalogs(), residential_only=not all_types_s)
            plans_s.to_csv(OUT / "sample_plans.csv", index=False)
            cov_s.to_csv(OUT / "sample_coverage.csv", index=False)
            ss.sample = (plans_s, cov_s)
        if ss.get("sample"):
            plans_s, cov_s = ss.sample
            if not cov_s.empty:
                st.markdown("**Providers per group: plan file found?**")
                grp_col = cov_s["grp"] if "grp" in cov_s else pd.Series("all", index=cov_s.index)
                st.dataframe(pd.crosstab([grp_col, cov_s["provider"]],
                                         cov_s["matched_by"].replace("", "no plan file")), width="stretch")
            if not plans_s.empty:
                st.markdown(f"**{len(plans_s)} plan tiers** across {plans_s['location_id'].nunique()} locations")
                st.dataframe(plans_s, hide_index=True, width="stretch")
                st.download_button("⬇️ sample_plans.csv", plans_s.to_csv(index=False), "sample_plans.csv")

    st.divider()
    st.markdown("#### Check the providers' websites for every sampled location")
    tasks_path = OUT / "scrape_tasks.csv"
    if not tasks_path.exists():
        st.info("No task list yet. Run `cls pipeline`.")
    else:
        from clscraper.scrapers import TUNED
        from clscraper.scrapers.isps import resolve_provider as _rp
        tasks = pd.read_csv(tasks_path, dtype=str).fillna("")
        # re-resolve, so ISPs set up after sampling are included
        tasks["isp"] = [_rp(b.split(";")[0], f.split(";")[0])[0] if b else k
                        for b, f, k in zip(tasks.get("nbm_brand", ""), tasks.get("nbm_frns", ""), tasks["isp"])]
        tasks = tasks[tasks["isp"].isin(TUNED)]
        st.write(f"{len(tasks)} (location, provider) website checks for "
                 f"{', '.join(SITE_NAMES.get(k, k) for k in sorted(tasks['isp'].unique()))}. "
                 "Results go to `outputs/scrape_results.csv`; finished checks are skipped on the next run.")
        limit = st.number_input("Max checks this run (0 = all)", 0, 10000, 10)
        if st.button("Check websites for the sampled locations"):
            from clscraper.scrapers.runner import scrape_tasks
            bar, line = st.progress(0.0), st.empty()

            def progress(i, n, res):
                bar.progress(i / max(n, 1))
                line.write(f"{i}/{n} · {SITE_NAMES.get(res.isp, res.isp)} · {res.location_id}: "
                           f"{STATUS_TEXT.get(res.status, ('', res.status))[1]}")

            scrape_tasks(cfg, tasks, headless=cfg["scraper"]["headless"], limit=limit or None,
                         progress=progress, log=lambda *_: None)
        res_path = OUT / "scrape_results.csv"
        if res_path.exists():
            res = pd.read_csv(res_path, dtype=str).fillna("")
            st.markdown("**Website checks by group × provider**")
            st.dataframe(pd.crosstab([res["grp"], res["isp"]], res["status"]), width="stretch")
            ok = res[res["upi"] != ""]
            if not ok.empty:
                st.dataframe(ok[["grp", "block_geoid", "location_id", "isp", "plan_name", "monthly_price",
                                 "typical_download_mbps", "typical_upload_mbps", "upi"]], hide_index=True,
                             width="stretch")
            st.download_button("⬇️ scrape_results.csv", res.to_csv(index=False), "scrape_results.csv")
