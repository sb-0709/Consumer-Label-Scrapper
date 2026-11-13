"""Thin BigQuery helpers: render SQL templates, typed parameters, cost guard."""
from __future__ import annotations

import pandas as pd

from .config import SQL_DIR

EXPECTED_COLUMNS = {
    "nbm_table": [
        "location_id", "block_geoid", "provider_id", "frn", "brand_name", "technology",
        "max_advertised_download_speed", "max_advertised_upload_speed",
        "business_residential_code", "state_usps",
    ],
    "fabric_table": [
        "location_id", "block_geoid", "address_primary", "city", "state", "zip",
        "bsl_flag", "building_type_code", "unit_count", "latitude", "longitude",
    ],
}


def get_client(cfg: dict):
    from google.cloud import bigquery

    b = cfg["bigquery"]
    return bigquery.Client(project=b["project"], location=b.get("location"))


def nbm_partition_filter(cfg: dict, states: list[str] | None = None, alias: str = "n") -> str:
    """Literal WHERE fragment on the hive partition columns (period, state).

    Must be literals, not query parameters, so BigQuery can skip the other GCS files.
    Folder names have used both "North Carolina" and "North_Carolina", so both are listed.
    """
    import re

    from .definitions import STATE_NAMES

    period = cfg["bigquery"].get("nbm_period")
    if not period:
        return ""
    if not re.fullmatch(r"\d{4}_\d{2}", str(period)):
        raise ValueError(f"bigquery.nbm_period must look like 2025_12, got {period!r}")
    parts = [f"AND {alias}.period = '{period}'"]
    states = cfg["scope"].get("states") if states is None else states
    if states:
        names = []
        for st in states:
            name = STATE_NAMES[str(st).upper()]
            names += [name, name.replace(" ", "_")]
        parts.append(f"AND {alias}.state IN ({', '.join(repr(n) for n in dict.fromkeys(names))})")
    return " ".join(parts)


def table_names(cfg: dict, states: list[str] | None = None) -> dict:
    b = cfg["bigquery"]
    out = b["output_dataset"]
    return {
        "nbm_partition_filter": nbm_partition_filter(cfg, states),
        "nbm_table": b["nbm_table"],
        "fabric_table": b["fabric_table"],
        "competition_table": f"{out}.block_competition",
        "selected_blocks_table": f"{out}.selected_blocks",
        "address_index_table": f"{out}.address_index",
    }


def render(sql_file: str, **fmt) -> str:
    return (SQL_DIR / sql_file).read_text().format(**fmt)


def to_params(params: dict):
    from google.cloud import bigquery

    out = []
    for name, v in params.items():
        if isinstance(v, bool):
            out.append(bigquery.ScalarQueryParameter(name, "BOOL", v))
        elif isinstance(v, int):
            out.append(bigquery.ScalarQueryParameter(name, "INT64", v))
        elif isinstance(v, float):
            out.append(bigquery.ScalarQueryParameter(name, "FLOAT64", v))
        elif isinstance(v, (list, tuple)):
            out.append(bigquery.ArrayQueryParameter(name, "STRING", [str(x) for x in v]))
        else:
            out.append(bigquery.ScalarQueryParameter(name, "STRING", None if v is None else str(v)))
    return out


def run_query(cfg: dict, sql: str, params: dict | None = None, *, fetch: bool = True,
              client=None, log=print) -> pd.DataFrame | None:
    """Dry-run first (prints GB scanned), then execute with a maximum_bytes_billed cap."""
    from google.cloud import bigquery

    client = client or get_client(cfg)
    qp = to_params(params or {})
    dry = client.query(sql, job_config=bigquery.QueryJobConfig(
        dry_run=True, use_query_cache=False, query_parameters=qp))
    gb = (dry.total_bytes_processed or 0) / 1e9
    cap_gb = float(cfg["bigquery"].get("max_gb_billed", 200))
    note = "  (external GCS tables are NOT in this estimate)" if gb < 0.5 else ""
    log(f"  BigQuery will scan ~{gb:,.2f} GB (cap {cap_gb:,.0f} GB){note}")
    job = client.query(sql, job_config=bigquery.QueryJobConfig(
        query_parameters=qp, maximum_bytes_billed=int(cap_gb * 1e9)))
    result = job.result()
    return result.to_dataframe() if fetch else None


def ensure_output_dataset(cfg: dict, client=None) -> None:
    from google.cloud import bigquery

    client = client or get_client(cfg)
    ds = bigquery.Dataset(cfg["bigquery"]["output_dataset"])
    ds.location = cfg["bigquery"].get("location", "US")
    client.create_dataset(ds, exists_ok=True)


def check_schemas(cfg: dict, client=None, log=print) -> bool:
    """Verify the NBM and Fabric tables have the columns the SQL expects."""
    client = client or get_client(cfg)
    names = table_names(cfg)
    ok = True
    for key, expected in EXPECTED_COLUMNS.items():
        t = client.get_table(names[key])
        cols = {f.name: f.field_type for f in t.schema}
        missing = [c for c in expected if c not in cols]
        log(f"\n{key}: {names[key]}  ({t.num_rows:,} rows)")
        for c in expected:
            log(f"   {'OK ' if c in cols else 'MISSING'}  {c:<32} {cols.get(c, '')}")
        if missing:
            ok = False
            log(f"   -> {len(missing)} column(s) missing. Create a VIEW that renames your columns "
                f"to these names and point config.yaml at the view.")
    return ok
