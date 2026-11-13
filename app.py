"""Broadband plan finder (Streamlit):  streamlit run app.py

1. Type an address  -> matched to the FCC Fabric; shows which ISPs the National Broadband Map lists
2. "Get plans"      -> opens each ISP's website, enters the address, reads every Broadband Facts label
3. See the plans    -> price, speeds, latency, fees, contract, Unique Plan Identifier; download CSV
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from clscraper.config import load_config, output_dir
from clscraper.definitions import TECH_NAMES
from clscraper.scrapers import CHECK_LABEL, SCRAPERS, TUNED, Address, resolve_provider

st.set_page_config(page_title="Broadband Plan Finder", page_icon="📡", layout="wide")
cfg = load_config()
OUT = output_dir(cfg)
ss = st.session_state

STATUS_TEXT = {
    "ok": ("✅", "Plans found"),
    "no_upi_found": ("⚠️", "Page loaded, but no Broadband Facts labels were found"),
    "not_serviceable": ("⛔", "ISP says it does not serve this address"),
    "address_issue": ("❓", "ISP could not match the address"),
    "blocked": ("🛑", "The site's bot protection refused the automated lookup — use Assisted mode"),
    "needs_assisted": ("🖐️", "No automatic scraper for this provider — turn on Assisted mode"),
    "call_to_order": ("📞", "ISP shows no online plans for this address (asks you to call) — no labels online"),
    "timeout": ("⏱️", "Timed out"),
    "error": ("❌", "Error"),
}
PLAN_COLUMNS = {
    "plan_name": "Plan", "monthly_price": "Monthly price ($)", "intro_months": "Intro (months)",
    "price_after_intro": "Price after intro ($)", "typical_download_mbps": "Typical down (Mbps)",
    "typical_upload_mbps": "Typical up (Mbps)", "typical_latency_ms": "Latency (ms)",
    "contract": "Contract", "data_included": "Data", "early_termination_fee": "Early termination fee",
    "upi": "Unique Plan Identifier",
}

# ---------------------------------------------------------------- sidebar
with st.sidebar:
    st.header("Settings")
    assisted = st.toggle("Assisted mode (recommended)", value=True,
                         help="A Chrome window opens with your address filled in. You pick the address from "
                              "the dropdown, open the plans and click each 'Broadband Facts'. The app reads "
                              "every page and collects the plan details. Close the window when done. "
                              "Needed because AT&T, Verizon and Spectrum block fully automated lookups.")
    show_browser = st.toggle("Show the browser windows", value=not cfg["scraper"]["headless"],
                             help="Visible browsers are blocked less often and let you see where a site gets stuck.")
    parallel = 1 if assisted else st.slider("ISPs checked at the same time", 1, 4, 2)
    st.divider()
    st.caption(f"NBM `{cfg['bigquery']['nbm_table']}` · period {cfg['bigquery'].get('nbm_period')}")
    st.caption(f"Fabric `{cfg['bigquery']['fabric_table']}`")
    st.caption(f"States indexed: {', '.join(cfg['scope'].get('states') or ['all'])}")

st.title("📡 Broadband Plan Finder")
st.write("Enter an address to see which internet providers serve it and the plans from their "
         "FCC **Broadband Facts** labels.")

tab_find, tab_batch, tab_results = st.tabs(["🔎 Find plans", "🧪 Research batch", "📊 Batch results"])

# ================================================================ FIND PLANS
with tab_find:
    with st.form("address"):
        c1, c2 = st.columns([4, 1])
        street = c1.text_input("Street address", placeholder="100 Main St")
        unit = c2.text_input("Apt / unit")
        c3, c4, c5 = st.columns([3, 1, 1])
        city = c3.text_input("City")
        state = c4.text_input("State", value=(cfg["scope"].get("states") or ["MD"])[0], max_chars=2)
        zip_ = c5.text_input("ZIP", max_chars=5)
        go = st.form_submit_button("Find providers", type="primary")

    if go:
        if not street or len(zip_) != 5:
            st.error("Please enter a street address and a 5-digit ZIP code.")
        else:
            ss.addr_input = dict(street=street, unit=unit, city=city, state=state.upper(), zip=zip_)
            ss.pop("results", None)
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
            st.warning("Couldn't search the address table, so all ISPs will be checked. "
                       "Run `cls build-index` once to enable lookups.\n\n" + ss.index_error[:300])
        elif not cands and a["state"] not in [s.upper() for s in (cfg["scope"].get("states") or [])]:
            st.info(f"The FCC address table only covers **{', '.join(cfg['scope'].get('states') or [])}**, "
                    f"so this {a['state']} address can't be matched to the FCC map. The ISP websites work "
                    f"for any US address, so you can still check them below.")
        elif not cands:
            st.info("No matching location in the Fabric for this ZIP. You can still check all ISPs.")
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

        st.subheader("2 · Providers at this address (FCC National Broadband Map)")
        default_isps = sorted(TUNED)
        all_prov = pd.DataFrame(loc["providers"]) if loc and loc["providers"] else pd.DataFrame()
        prov = all_prov[~all_prov["technology"].isin([60, 61])] if not all_prov.empty else all_prov
        if not all_prov.empty and len(prov) < len(all_prov):
            st.caption(f"Satellite providers (GSO 60 / NGSO-LEO 61) hidden: "
                       f"{', '.join(sorted(set(all_prov[all_prov['technology'].isin([60, 61])]['brand_name'])))}")
        if loc and not prov.empty:
            prov = prov.copy()
            min_down, min_up = float(cfg["speed"]["min_down"]), float(cfg["speed"]["min_up"])
            prov[f"Meets {min_down:g}/{min_up:g}"] = [
                "✅" if d >= min_down and u >= min_up else "—" for d, u in zip(prov["down"], prov["up"])]
            prov["Technology"] = prov["technology"].map(TECH_NAMES)
            prov["_key"] = [resolve_provider(b)[0] for b in prov["brand_name"]]   # registers search scrapers
            prov["We can check"] = [CHECK_LABEL[resolve_provider(b)[1]] for b in prov["brand_name"]]
            st.dataframe(
                prov.rename(columns={"brand_name": "Provider", "down": "Max down (Mbps)", "up": "Max up (Mbps)",
                                     "frn": "FRN"})[["Provider", "Technology", "Max down (Mbps)",
                                                     "Max up (Mbps)", f"Meets {min_down:g}/{min_up:g}",
                                                     "FRN", "We can check"]],
                hide_index=True, width="stretch")
            default_isps = list(dict.fromkeys(prov["_key"]))
        elif loc:
            st.info("The map lists no terrestrial (non-satellite) providers at this location.")

        options = sorted(TUNED) + [k for k in SCRAPERS if k not in TUNED]
        isps = st.multiselect("ISP websites to check", options, default=[k for k in default_isps if k in options],
                              format_func=lambda k: SCRAPERS[k].name + ("" if k in TUNED else "  (assisted)"))
        if not assisted and any(k not in TUNED for k in isps):
            st.caption("Providers marked *(assisted)* only work with **Assisted mode** on (sidebar).")
        if st.button("Get plans", type="primary", disabled=not isps):
            from clscraper.scrapers.runner import append_rows, scrape_address
            if loc:
                addr = Address(loc["address_primary"], loc["city"], loc["state"], loc["zip"],
                               unit=a["unit"], location_id=loc["location_id"])
            else:
                addr = Address(a["street"], a["city"], a["state"], a["zip"], unit=a["unit"])
            expected = {}
            for p in (loc or {}).get("providers", []):
                expected.setdefault(resolve_provider(p["brand_name"])[0], set()).add(p["frn"])
            with st.status(f"Checking {len(isps)} ISP website(s) for {addr.one_line()} ...", expanded=True) as box:
                live = st.empty()

                def on_start(isp):
                    if assisted:
                        box.info(f"**{SCRAPERS[isp].name}**: a Chrome window just opened with the address typed in.\n\n"
                                 "1. Pick your address from the dropdown (add the apartment if asked) and continue.\n"
                                 "2. On the plans page, click **Broadband Facts** / the label link for each plan.\n"
                                 "3. **Close the Chrome window** when you're done. The next ISP opens after that.")

                def on_update(r):
                    live.write(f"📋 {SCRAPERS[r.isp].name}: {len(r.upis)} plan label(s) captured so far")

                results = scrape_address(cfg, addr, isps, headless=not show_browser, parallel=parallel,
                                         assist=assisted, on_start=on_start, on_update=on_update,
                                         log=lambda *_: None,
                                         on_done=lambda r: box.write(f"{STATUS_TEXT.get(r.status, ('', ''))[0]} "
                                                                     f"{SCRAPERS[r.isp].name}: {len(r.upis)} plan(s)"))
                box.update(label="Done", state="complete")
            rows = []
            for r in results:
                rows += r.rows(extra={"nbm_frns": ";".join(sorted(expected.get(r.isp, [])))})
            append_rows(OUT / "lookup_results.csv", rows)
            ss.results = (results, rows)

        with st.expander("📥 Import from your own browser (when a site blocks the app's browser)"):
            st.markdown(
                "1. In your **normal Chrome**, open the ISP's site and look up this address.\n"
                "2. On the plans page, open the **Broadband Facts** label(s) (or a *view all labels* link).\n"
                "3. Save the page with **⌘S** (*Webpage, Complete* or *Single File*), or select and copy the label text.\n"
                "4. Pick the ISP below and drop the file(s) here, or paste the text.")
            imp_isp = st.selectbox("ISP these pages are from", options,
                                   format_func=lambda k: SCRAPERS[k].name, key="imp_isp")
            files = st.file_uploader("Saved pages (.html, .mhtml, .txt)", type=["html", "htm", "mhtml", "mht", "txt"],
                                     accept_multiple_files=True, key="imp_files")
            pasted = st.text_area("…or paste label text", height=120, key="imp_text")
            if st.button("Read labels", disabled=not (files or pasted.strip())):
                from clscraper.importer import file_to_chunks, import_labels
                from clscraper.scrapers.runner import append_rows
                chunks = [c for f in (files or []) for c in file_to_chunks(f.name, f.getvalue())]
                if pasted.strip():
                    chunks.append(("import:pasted text", pasted))
                if loc:
                    iaddr = Address(loc["address_primary"], loc["city"], loc["state"], loc["zip"],
                                    unit=a["unit"], location_id=loc["location_id"])
                else:
                    iaddr = Address(a["street"], a["city"], a["state"], a["zip"], unit=a["unit"])
                r = import_labels(imp_isp, iaddr, chunks)
                frns = ";".join(sorted({p["frn"] for p in (loc or {}).get("providers", [])
                                        if resolve_provider(p["brand_name"])[0] == imp_isp}))
                irows = r.rows(extra={"nbm_frns": frns})
                append_rows(OUT / "lookup_results.csv", irows)
                prev_results, prev_rows = ss.get("results") or ([], [])
                ss.results = ([x for x in prev_results if x.isp != imp_isp] + [r],
                              [x for x in prev_rows if x.get("isp") != imp_isp] + irows)
                (st.success if r.upis else st.warning)(
                    f"{len(r.upis)} plan label(s) read from {SCRAPERS[imp_isp].name}."
                    + ("" if r.upis else " No Unique Plan Identifier found. Make sure the label was open when you saved."))

        if ss.get("results"):
            results, rows = ss.results
            st.subheader("3 · Plans")
            all_plans = pd.DataFrame([r for r in rows if r.get("upi")])
            if not all_plans.empty:
                st.download_button("⬇️ Download all plans (CSV)", all_plans.to_csv(index=False),
                                   "broadband_plans.csv", "text/csv")
            for r in results:
                icon, text = STATUS_TEXT.get(r.status, ("•", r.status))
                with st.container(border=True):
                    st.markdown(f"### {SCRAPERS[r.isp].name}  \n{icon} {text}"
                                + (f" — **{len(r.upis)} plan(s)**" if r.upis else ""))
                    if r.upis:
                        df = pd.DataFrame(r.upis)
                        cols = [c for c in PLAN_COLUMNS if c in df.columns]
                        st.dataframe(df[cols].rename(columns=PLAN_COLUMNS), hide_index=True,
                                     width="stretch")
                        for h in r.upis:
                            with st.expander(f"Label text · {h.get('plan_name') or h['upi']}"):
                                st.write(h.get("label_text") or h.get("context") or "")
                                if h.get("provider_monthly_fees"):
                                    st.write("**Provider monthly fees:**", h["provider_monthly_fees"])
                                if h.get("one_time_fees"):
                                    st.write("**One-time fees:**", h["one_time_fees"])
                    if r.message:
                        st.caption(r.message)
                    if r.final_url:
                        st.caption(f"[Open the ISP page]({r.final_url})")
                    if r.screenshot:
                        with st.expander("Screenshot of what the scraper saw"):
                            st.image(r.screenshot)

# ================================================================ BATCH
with tab_batch:
    tasks_path = OUT / "scrape_tasks.csv"
    st.write("Runs the sampled most/least competitive locations from `cls pipeline`.")
    if not tasks_path.exists():
        st.info("No task list yet. Run `cls pipeline` to sample locations from BigQuery.")
    else:
        tasks = pd.read_csv(tasks_path, dtype=str).fillna("")
        st.write(f"{len(tasks)} lookups in `{tasks_path.name}`")
        st.dataframe(tasks.groupby(["grp", "isp"]).size().rename("lookups").reset_index(), hide_index=True)
        sel = st.multiselect("ISPs", sorted(tasks["isp"].unique()), default=sorted(tasks["isp"].unique()))
        limit = st.number_input("Max lookups this run (0 = all)", 0, 10000, 5)
        if st.button("Run batch", type="primary"):
            from clscraper.scrapers.runner import scrape_tasks
            bar, status = st.progress(0.0), st.empty()

            def progress(i, n, res):
                bar.progress(i / max(n, 1))
                status.write(f"{i}/{n}  {res.isp} {res.location_id}: {res.status}")

            scrape_tasks(cfg, tasks, isps=sel, headless=not show_browser, limit=limit or None,
                         progress=progress, log=lambda *_: None, assist=assisted)
            st.success("Done. See the Batch results tab.")

with tab_results:
    res_path = OUT / "scrape_results.csv"
    if res_path.exists():
        res = pd.read_csv(res_path, dtype=str).fillna("")
        st.subheader("Status by group × ISP")
        st.dataframe(pd.crosstab([res["grp"], res["isp"]], res["status"]), width="stretch")
        st.subheader("Plans found")
        cols = ["grp", "block_geoid", "location_id", "isp"] + [c for c in PLAN_COLUMNS if c in res] + ["frn_matches_nbm"]
        st.dataframe(res[res["upi"] != ""][cols], width="stretch", hide_index=True)
        st.download_button("Download scrape_results.csv", res.to_csv(index=False), "scrape_results.csv")
    else:
        st.info("No batch results yet.")
