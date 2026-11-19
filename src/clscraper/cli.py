"""Command line entry point:  cls <command>   (or: python -m clscraper.cli <command>)

  cls check                      verify BigQuery access + NBM/Fabric column names
  cls competition                STEP 1  block-level competition table
  cls select                     STEP 2  most / least competitive blocks
  cls sample                     STEP 3  random locations + Fabric addresses -> scrape_tasks.csv
  cls pipeline                   STEPS 1-3 in one go
  cls scrape                     STEP 4  run ISP lookups for outputs/scrape_tasks.csv
  cls lookup --address "..."     one address, chosen ISPs (no BigQuery needed)
  cls build-index                one-time address lookup table for the web app
  cls catalogs [--refresh]       download / list the ISPs' machine-readable plan files
  cls coverage                   every NBM provider in the indexed states + whether it has a plan file
  cls plans --location-id ID     plans at a location from the plan files (no ISP website needed)
  cls plans --street "..." --zip 21201
  cls summary                    STEP 5  results by group x ISP
"""
from __future__ import annotations

import argparse
import sys

import pandas as pd

from .config import load_config, output_dir


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="cls", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", help="path to config.yaml")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check")
    sub.add_parser("competition")
    sub.add_parser("select")
    sub.add_parser("sample")
    sub.add_parser("pipeline")

    sp = sub.add_parser("scrape")
    sp.add_argument("--tasks", help="CSV of tasks (default outputs/scrape_tasks.csv)")
    sp.add_argument("--isp", nargs="*", help="only these ISP keys")
    sp.add_argument("--limit", type=int, help="stop after N lookups (good for testing)")
    sp.add_argument("--manual", action="store_true", help="pause for you to finish in the browser when automation fails")
    sp.add_argument("--headless", action="store_true")
    sp.add_argument("--no-resume", action="store_true", help="redo lookups already in scrape_results.csv")

    lp = sub.add_parser("lookup")
    g = lp.add_mutually_exclusive_group(required=True)
    g.add_argument("--address", help='"123 Main St, Durham, NC 27701"')
    g.add_argument("--location-id", help="Fabric location_id (address + ISPs fetched from BigQuery)")
    lp.add_argument("--isp", nargs="*", help="ISP keys (default: all, or those NBM lists for the location)")
    lp.add_argument("--manual", action="store_true")
    lp.add_argument("--headless", action="store_true")

    sub.add_parser("summary")
    sub.add_parser("build-index")
    sub.add_parser("coverage")
    cp = sub.add_parser("catalogs")
    cp.add_argument("--refresh", action="store_true", help="download every configured plan file now")
    pp = sub.add_parser("plans")
    g2 = pp.add_mutually_exclusive_group(required=True)
    g2.add_argument("--location-id")
    g2.add_argument("--street", help="street address (with --zip), matched in the address index")
    pp.add_argument("--zip")
    pp.add_argument("--all-types", action="store_true", help="include low-income, prepaid, business plans")
    a = ap.parse_args(argv)
    cfg = load_config(a.config)

    if a.cmd == "build-index":
        from .pipeline import build_address_index
        build_address_index(cfg)
        return 0

    if a.cmd == "check":
        from .bq import check_schemas
        return 0 if check_schemas(cfg) else 1

    if a.cmd in ("competition", "pipeline"):
        from .pipeline import build_competition
        print(build_competition(cfg).to_string(index=False))
    if a.cmd in ("select", "pipeline"):
        from .pipeline import select_blocks
        cols = ["grp", "block_geoid", "n_bsl", "n_isp", "avg_isp", "providers_terrestrial"]
        df = select_blocks(cfg)
        print(df[[c for c in cols if c in df]].to_string(index=False))
    if a.cmd in ("sample", "pipeline"):
        from .pipeline import sample_locations
        _, tasks = sample_locations(cfg)
        if not tasks.empty:
            print(tasks.groupby(["grp", "isp"]).size().rename("lookups").to_string())
    if a.cmd in ("competition", "select", "sample", "pipeline"):
        return 0

    if a.cmd == "scrape":
        from .scrapers.runner import scrape_tasks
        path = a.tasks or output_dir(cfg) / "scrape_tasks.csv"
        tasks = pd.read_csv(path, dtype=str).fillna("")
        out = scrape_tasks(cfg, tasks, isps=a.isp, manual=a.manual, headless=a.headless or None,
                           resume=not a.no_resume, limit=a.limit)
        print(f"\nResults: {out}")
        return 0

    if a.cmd == "lookup":
        from .scrapers import TUNED, Address
        from .scrapers.runner import scrape_address
        isps = a.isp
        if a.location_id:
            from .pipeline import lookup_location
            from .scrapers.isps import resolve_provider
            rec = lookup_location(cfg, a.location_id)
            if rec is None:
                print("location_id not found in the Fabric table")
                return 1
            addr = Address(rec["address_primary"], rec["city"], rec["state"], str(rec["zip"]),
                           location_id=a.location_id)
            print("NBM providers here:")
            for p in rec["providers"]:
                print(f"   {p['brand_name']:<35} tech {p['technology']:<3} {p['down']:>7.0f}/{p['up']:<6.0f}"
                      f" FRN {p['frn']}  -> {resolve_provider(p['brand_name'])}")
            isps = isps or sorted({resolve_provider(p["brand_name"])[0] for p in rec["providers"]
                                   if p["technology"] not in (60, 61)})
        else:
            addr = Address.parse(a.address)
        isps = isps or sorted(TUNED)
        results = scrape_address(cfg, addr, isps, headless=a.headless or None, manual=a.manual)
        rows = [r for res in results for r in res.rows()]
        df = pd.DataFrame(rows)
        print(df[["isp", "status", "upi", "frn", "message"]].to_string(index=False))
        from .scrapers.runner import append_rows
        append_rows(output_dir(cfg) / "lookup_results.csv", rows)
        return 0

    if a.cmd == "catalogs":
        from . import catalog
        if a.refresh:
            for k in catalog.status().query("auto_download")["isp"]:
                try:
                    catalog.download(k)
                except Exception as e:  # noqa: BLE001
                    print(f"{k}: {e}")
        else:
            for k, err in catalog.ensure_fresh().items():
                print(f"{k}: {err}")
        print(catalog.status().to_string(index=False))
        return 0

    if a.cmd == "coverage":
        from . import catalog
        from .pipeline import provider_coverage
        rep = catalog.coverage_report(provider_coverage(cfg), catalog.load_all())
        print(rep[["brand_name", "frn", "technologies", "n_locations", "status"]].to_string(index=False))
        done = rep["plan_file"] != ""
        print(f"\n{done.sum()}/{len(rep)} providers have a plan file, covering "
              f"{rep.loc[done, 'n_locations'].sum() / max(rep['n_locations'].sum(), 1):.0%} of provider-locations")
        return 0

    if a.cmd == "plans":
        from . import catalog
        if a.location_id:
            from .pipeline import lookup_location
            rec = lookup_location(cfg, a.location_id)
        else:
            from .pipeline import search_address
            cands = search_address(cfg, a.street, a.zip or "")
            rec = cands[0] if cands else None
        if not rec:
            print("No matching Fabric location.")
            return 1
        print(f"{rec['address_primary']}, {rec['city']} {rec['state']} {rec['zip']}  (location_id {rec['location_id']})")
        catalog.ensure_fresh()
        plans, coverage = catalog.match_location(rec["providers"], catalog.load_all(),
                                                 residential_only=not a.all_types)
        print(coverage.to_string(index=False))
        summary = catalog.summarise(plans)
        if not summary.empty:
            print(summary[["nbm_provider", "nbm_technology", "plan_name", "down_mbps", "up_mbps",
                           "monthly_price", "intro_price", "n_upis", "upi"]].to_string(index=False))
            out = output_dir(cfg) / f"plans_{rec['location_id']}.csv"
            summary.assign(location_id=rec["location_id"], address=rec["address_primary"]).to_csv(out, index=False)
            print(f"\nSaved {out}")
        return 0

    if a.cmd == "summary":
        path = output_dir(cfg) / "scrape_results.csv"
        df = pd.read_csv(path, dtype=str).fillna("")
        print(df.groupby(["grp", "isp", "status"])["location_id"].nunique().rename("locations").to_string())
        ok = df[df["upi"] != ""]
        print("\nDistinct Unique Plan Identifiers per group x ISP:")
        print(ok.groupby(["grp", "isp"])["upi"].nunique().to_string())
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
