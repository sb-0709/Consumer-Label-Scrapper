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

tab_find, tab_files, tab_sample = st.tabs(["🔎 Find plans", "🗂️ ISP plan files", "🧪 Research sample"])

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
                prov["Plan file"] = ["✅" if catalog._catalog_for(p, cats)[1] else "❌ missing"
                                     for p in prov.to_dict("records")]
                st.dataframe(prov.rename(columns={"brand_name": "Provider", "down": "Max down (Mbps)",
                                                  "up": "Max up (Mbps)", "frn": "FRN"})[
                    ["Provider", "Technology", "Max down (Mbps)", "Max up (Mbps)",
                     f"Meets {min_down:g}/{min_up:g}", "FRN", "Plan file"]], hide_index=True, width="stretch")

            st.subheader("3 · Plans at this address")
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
            st.caption("How plans are matched: same provider (FRN in the Unique Plan Identifier, or brand name), "
                       "same technology, and plan speed ≤ the max speed the provider reported to the FCC for this "
                       "location. Plan files carry no geography, so these are plans consistent with the FCC map "
                       "at this address.")

# ================================================================ ISP PLAN FILES
with tab_files:
    st.subheader("Which ISPs have a plan file?")
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
