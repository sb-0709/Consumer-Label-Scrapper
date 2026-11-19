"""Steps 1-3: competition -> select blocks -> sample locations (+ scrape task list)."""
from __future__ import annotations

import json
import re

import pandas as pd

from . import bq
from .config import output_dir
from .definitions import DEFINITIONS, qualifies


def build_competition(cfg: dict, log=print) -> pd.DataFrame:
    """STEP 1: per-block ISP counts; returns the distribution of blocks by #ISPs."""
    client = bq.get_client(cfg)
    bq.ensure_output_dataset(cfg, client)
    t = bq.table_names(cfg)
    log(f"STEP 1  Building {t['competition_table']} ...")
    bq.run_query(cfg, bq.render("01_block_competition.sql", **t), {
        "min_down": float(cfg["speed"]["min_down"]),
        "min_up": float(cfg["speed"]["min_up"]),
        "residential_only": bool(cfg["scope"]["residential_only"]),
        "states": list(cfg["scope"].get("states") or []),
        "competitive_threshold": int(cfg["competition"]["competitive_threshold"]),
    }, fetch=False, client=client, log=log)

    unions = " UNION ALL ".join(
        f"SELECT '{d}' AS definition, n_isp_{d} AS n_isps, COUNT(*) AS n_blocks, SUM(n_bsl) AS n_bsl "
        f"FROM `{t['competition_table']}` GROUP BY 2" for d in DEFINITIONS
    )
    dist = bq.run_query(cfg, f"SELECT * FROM ({unions}) ORDER BY definition, n_isps", client=client, log=log)
    path = output_dir(cfg) / "competition_distribution.csv"
    dist.to_csv(path, index=False)
    log(f"  Saved distribution -> {path}")
    return dist


def select_blocks(cfg: dict, log=print) -> pd.DataFrame:
    """STEP 2: N most and N least competitive blocks under the chosen definition."""
    definition = cfg["competition"]["definition"]
    if definition not in DEFINITIONS:
        raise ValueError(f"competition.definition must be one of {list(DEFINITIONS)}")
    client = bq.get_client(cfg)
    t = bq.table_names(cfg)
    log(f"STEP 2  Selecting most/least competitive blocks (definition = {definition}) ...")
    bq.run_query(cfg, bq.render("02_select_blocks.sql", definition=definition, **t), {
        "seed": str(cfg["sampling"]["seed"]),
        "min_bsl": int(cfg["competition"]["min_bsl_in_block"]),
        "n_blocks": int(cfg["competition"]["n_blocks_per_group"]),
    }, fetch=False, client=client, log=log)
    df = bq.run_query(cfg, f"SELECT * FROM `{t['selected_blocks_table']}` ORDER BY grp, n_isp DESC",
                      client=client, log=log)
    path = output_dir(cfg) / "selected_blocks.csv"
    df.to_csv(path, index=False)
    log(f"  {len(df)} blocks -> {path}")
    return df


def sample_locations(cfg: dict, log=print) -> tuple[pd.DataFrame, pd.DataFrame]:
    """STEP 3: random locations in the selected blocks, with Fabric addresses and NBM providers.

    Returns (locations, tasks). *tasks* has one row per (location, supported ISP) to scrape.
    """
    t = bq.table_names(cfg)
    log("STEP 3  Sampling locations and joining Fabric v8 addresses ...")
    locs = bq.run_query(cfg, bq.render("03_sample_locations.sql", **t), {
        "seed": str(cfg["sampling"]["seed"]),
        "per_block": int(cfg["sampling"]["locations_per_block"]),
        "max_per_group": int(cfg["sampling"]["max_locations_per_group"]),
        "residential_only": bool(cfg["scope"]["residential_only"]),
    }, log=log)
    locs_path = output_dir(cfg) / "sample_locations.csv"
    locs.to_csv(locs_path, index=False)
    tasks = build_tasks(locs, cfg)
    tasks_path = output_dir(cfg) / "scrape_tasks.csv"
    tasks.to_csv(tasks_path, index=False)
    log(f"  {len(locs)} locations -> {locs_path}")
    log(f"  {len(tasks)} (location, ISP) lookups -> {tasks_path}")
    return locs, tasks


def build_tasks(locs: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Expand each sampled location into the supported ISPs that NBM says serve it."""
    definition = cfg["competition"]["definition"]
    min_down, min_up = float(cfg["speed"]["min_down"]), float(cfg["speed"]["min_up"])
    from .scrapers.isps import resolve_provider

    rows = []
    for _, r in locs.iterrows():
        providers = json.loads(r.get("providers_json") or "[]")
        by_isp: dict[str, dict] = {}
        for p in providers:
            if not qualifies(p, definition, min_down, min_up):
                continue
            key, kind = resolve_provider(p.get("brand_name"))
            entry = by_isp.setdefault(key, {"frns": set(), "brands": set(), "techs": set(), "max_down": 0.0,
                                            "kind": kind})
            entry["frns"].add(p.get("frn"))
            entry["brands"].add(p.get("brand_name"))
            entry["techs"].add(str(p.get("technology")))
            entry["max_down"] = max(entry["max_down"], float(p.get("down") or 0))
        for key, e in by_isp.items():
            rows.append({
                "grp": r["grp"], "block_geoid": r["block_geoid"], "location_id": r["location_id"],
                "address_primary": r["address_primary"], "city": r["city"], "state": r["state"],
                "zip": str(r["zip"]).zfill(5) if pd.notna(r["zip"]) else "",
                "isp": key, "nbm_brand": "; ".join(sorted(e["brands"])),
                "nbm_frns": ";".join(sorted(f for f in e["frns"] if f)),
                "nbm_technologies": ";".join(sorted(e["techs"])),
                "nbm_max_down": e["max_down"],
                "check_mode": e["kind"],   # tuned = automatic works; known/search = assisted only
            })
    return pd.DataFrame(rows)


def lookup_location(cfg: dict, location_id: str, log=print) -> dict | None:
    """Fabric address for one location_id, then the NBM providers from that state's partition only."""
    t = bq.table_names(cfg)
    df = bq.run_query(cfg, bq.render("lookup_location.sql", **t), {"location_id": str(location_id)}, log=log)
    if df.empty:
        return None
    rec = df.iloc[0].to_dict()
    t_state = bq.table_names(cfg, states=[rec["state"]])
    prov = bq.run_query(cfg, bq.render("lookup_providers.sql", **t_state),
                        {"location_id": str(location_id)}, log=log)
    rec["providers"] = prov.to_dict("records")
    return rec


def index_states(cfg: dict) -> list[str]:
    """States the app's address lookup covers ([] = every state). Defaults to scope.states."""
    app = cfg.get("app") or {}
    return list(app["index_states"] if "index_states" in app else (cfg["scope"].get("states") or []))


def location_source(cfg: dict) -> tuple[str, str]:
    """(nested location table, NBM period) when app.location_table is set, else ("", nbm_period)."""
    app = cfg.get("app") or {}
    table = (app.get("location_table") or "").strip()
    return table, str(app.get("period") or cfg["bigquery"].get("nbm_period") or "")


def build_address_index(cfg: dict, log=print) -> None:
    """One-time: addresses + NBM providers for app.index_states (for the web app).

    Source: app.location_table (nested table with every NBM release) if set, otherwise NBM + Fabric.
    """
    client = bq.get_client(cfg)
    bq.ensure_output_dataset(cfg, client)
    states = index_states(cfg)
    t = bq.table_names(cfg, states=states)
    table, period = location_source(cfg)
    if table:
        log(f"Building {t['address_index_table']} from {table}, NBM period {period}, {states or 'ALL states'} ...")
        bq.run_query(cfg, bq.render("04b_build_index_from_locations.sql", location_table=table, **t),
                     {"states": states, "period": period}, fetch=False, client=client, log=log)
    else:
        log(f"Building {t['address_index_table']} from NBM + Fabric for {states or 'ALL states'} ...")
        bq.run_query(cfg, bq.render("04_build_address_index.sql", **t),
                     {"states": states}, fetch=False, client=client, log=log)
    n = bq.run_query(cfg, f"SELECT COUNT(*) AS n FROM `{t['address_index_table']}`", client=client, log=log)
    log(f"  {int(n.iloc[0]['n']):,} locations indexed")


def provider_coverage(cfg: dict, log=print) -> pd.DataFrame:
    """Terrestrial providers in the indexed states, biggest first -> outputs/provider_coverage.csv."""
    states = index_states(cfg)
    t = bq.table_names(cfg, states=states)
    table, period = location_source(cfg)
    if table:
        df = bq.run_query(cfg, bq.render("provider_coverage_from_locations.sql", location_table=table, **t),
                          {"states": states, "period": period}, log=log)
    else:
        df = bq.run_query(cfg, bq.render("provider_coverage.sql", **t), log=log)
    df.to_csv(output_dir(cfg) / "provider_coverage.csv", index=False)
    return df


def search_address(cfg: dict, street: str, zip_code: str, top: int = 8, log=print) -> list[dict]:
    """Fabric locations matching a typed street + ZIP, best match first (with NBM providers)."""
    from .address_match import house_number, rank_candidates

    t = bq.table_names(cfg)
    zip5 = re.sub(r"\D", "", str(zip_code))[:5]
    df = bq.run_query(cfg, bq.render("search_address.sql", **t),
                      {"zip": zip5, "house": house_number(street)}, log=log)
    if df.empty and house_number(street):
        df = bq.run_query(cfg, bq.render("search_address.sql", **t), {"zip": zip5, "house": ""}, log=log)
    cands = df.to_dict("records")
    for c in cands:
        c["providers"] = json.loads(c.pop("providers_json") or "[]")
    return rank_candidates(street, cands, top)
