"""ISP plan files (machine-readable Broadband Facts) and matching them to a location.

Why: AT&T, Verizon and Spectrum block automated address lookups on their websites. But the
FCC requires every ISP to publish ALL its current labels in one machine-readable file
(47 CFR 8.1(a)(3)). We download that file once per ISP and, for a given location, keep the
plans that fit what the National Broadband Map (NBM) says the ISP offers there:

    same ISP (FRN or brand)  +  same technology (fiber / cable / copper / fixed wireless)
    +  plan speed <= the max advertised speed the ISP reported for that location

The files carry no geography, so this is "plans consistent with the NBM at this location",
not an address-level confirmation from the ISP. Every row says how it was matched.
"""
from __future__ import annotations

import io
import re
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import yaml

from .config import ROOT
from .definitions import match_isp

CATALOG_DIR = ROOT / "data" / "catalogs"
USER_AGENT = "clscraper/0.1 (academic research; FCC machine-readable broadband labels)"

COLUMNS = [
    "isp", "provider", "frn", "upi", "plan_name", "segment", "connection", "technology",
    "down_mbps", "up_mbps", "typical_down_mbps", "typical_up_mbps", "latency_ms",
    "monthly_price", "intro_price", "intro_period", "contract", "monthly_fees", "one_time_fees",
    "early_termination_fee", "data_included", "speed_basis", "catalog_file",
]
# NBM technology -> plan technologies that can serve it
TECH_COMPAT = {10: {10}, 40: {40}, 50: {50}, 70: {70, 71, 72}, 71: {70, 71, 72}, 72: {70, 71, 72}}
SPEED_TOLERANCE = 1.10      # plan speed may exceed the NBM max by 10% (rounding, "1 Gig" = 1000 vs 940 ...)


# ------------------------------------------------------------------------------ config + download
def load_sources() -> dict:
    path = ROOT / "catalogs.yaml"
    return yaml.safe_load(path.read_text()) if path.exists() else {"catalogs": {}}


def _isp_dir(isp: str) -> Path:
    d = CATALOG_DIR / isp
    d.mkdir(parents=True, exist_ok=True)
    return d


LABELS_FILE = "labels.csv"     # plans built from label pages / PDFs / pasted text
SITE_FILE = "site_plans.csv"   # plans collected from the provider's website (normalised columns)
SITE_URL_FILE = "site.txt"     # the website it was collected from (re-crawled when stale)
DERIVED = {LABELS_FILE, SITE_FILE}


def latest_file(isp: str) -> Path | None:
    """Newest downloaded / uploaded machine-readable file (not the labels.csv built from pages)."""
    d = CATALOG_DIR / isp
    files = sorted((f for f in d.glob("*.csv") if f.name not in DERIVED),
                   key=lambda p: p.stat().st_mtime) if d.exists() else []
    return files[-1] if files else None


def labels_file(isp: str, name: str = LABELS_FILE) -> Path | None:
    f = CATALOG_DIR / isp / name
    return f if f.exists() else None


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=180) as r:   # noqa: S310 - URLs come from catalogs.yaml
        return r.read()


def download(isp: str, log=print) -> Path:
    """Fetch the ISP's plan file (one request) and keep it as data/catalogs/<isp>/<date>.csv."""
    src = load_sources()["catalogs"].get(isp) or {}
    url = (src.get("url") or "").strip()
    if not url:
        raise ValueError(f"No plan-file URL for '{isp}' in catalogs.yaml. Download it in your browser "
                         f"(see {src.get('source_page') or 'the ISP disclosures page'}) and add it in the app.")
    tries = [url.replace("{date}", (date.today() - timedelta(days=d)).isoformat()) for d in range(4)] \
        if "{date}" in url else [url]
    last = None
    for u in tries:
        try:
            data = _get(u)
            if data[:200].lstrip().startswith(b"<"):
                raise ValueError("got a web page instead of a CSV")
            out = _isp_dir(isp) / f"{date.today().isoformat()}.csv"
            out.write_bytes(data)
            log(f"{isp}: {len(data) / 1e6:.1f} MB from {u}")
            return out
        except Exception as e:  # noqa: BLE001 - try the previous day's file name
            last = e
    raise RuntimeError(f"{isp}: could not download the plan file ({last})")


def save_uploaded(isp: str, name: str, data: bytes) -> Path:
    """A plan file the user downloaded in their own browser."""
    normalise(pd.read_csv(io.BytesIO(data), dtype=str, nrows=50), isp, name)   # fail early if unreadable
    out = _isp_dir(isp) / f"{date.today().isoformat()}_upload_{re.sub(r'[^A-Za-z0-9.]+', '_', name)}"
    if not out.name.endswith(".csv"):
        out = out.with_suffix(".csv")
    out.write_bytes(data)
    return out


def status() -> pd.DataFrame:
    """One row per ISP in catalogs.yaml (plus any ISP folder with an uploaded file)."""
    srcs = load_sources()["catalogs"]
    keys = list(srcs) + [p.name for p in CATALOG_DIR.glob("*") if p.is_dir() and p.name not in srcs] \
        if CATALOG_DIR.exists() else list(srcs)
    rows = []
    for k in keys:
        f, lab, site = latest_file(k), labels_file(k), labels_file(k, SITE_FILE)
        files = [x for x in (f, lab, site) if x]
        newest = max(files, key=lambda p: p.stat().st_mtime, default=None)
        name = (srcs.get(k) or {}).get("name") or _label_provider_name(lab) or _label_provider_name(site) or k
        rows.append({"isp": k, "name": name,
                     "file": " + ".join(x.name for x in files),
                     "downloaded": datetime.fromtimestamp(newest.stat().st_mtime).strftime("%Y-%m-%d")
                     if newest else "", "auto_download": bool((srcs.get(k) or {}).get("url"))})
    return pd.DataFrame(rows, columns=["isp", "name", "file", "downloaded", "auto_download"])


def _label_provider_name(path: Path | None) -> str:
    if not path:
        return ""
    try:
        df = pd.read_csv(path, dtype=str, nrows=1)
        return str(df.iloc[0][[c for c in ("provider_name", "provider") if c in df.columns][0]]).strip()
    except Exception:  # noqa: BLE001
        return ""


def ensure_fresh(log=print) -> dict[str, str]:
    """Download every configured file that is missing or older than max_age_days. Returns errors."""
    cfg = load_sources()
    max_age = timedelta(days=int(cfg.get("max_age_days", 7)))
    errors = {}
    for isp, src in cfg["catalogs"].items():
        f = latest_file(isp)
        if not src.get("url") or (f and datetime.now() - datetime.fromtimestamp(f.stat().st_mtime) < max_age):
            continue
        try:
            download(isp, log=log)
        except Exception as e:  # noqa: BLE001
            errors[isp] = str(e)
    for site_txt in CATALOG_DIR.glob(f"*/{SITE_URL_FILE}") if CATALOG_DIR.exists() else []:
        isp, plans = site_txt.parent.name, site_txt.parent / SITE_FILE
        if plans.exists() and datetime.now() - datetime.fromtimestamp(plans.stat().st_mtime) < max_age:
            continue
        url, *rest = site_txt.read_text().splitlines() + ["", ""]
        try:
            crawl_site(isp, url.strip(), provider_name=rest[0].strip(), log=log)
        except Exception as e:  # noqa: BLE001
            errors[isp] = str(e)
    return errors


# ------------------------------------------------------------------------------ parsing
_NUM = r"(\d+(?:\.\d+)?)"


def _num(s) -> float | None:
    m = re.search(_NUM, str(s or "").replace(",", ""))
    return float(m.group(1)) if m else None


def _to_mbps(value, unit: str = "mbps") -> float | None:
    v = _num(value)
    if v is None:
        return None
    unit = (unit or "mbps").lower()
    return v * 1000 if unit.startswith("g") else v / 1000 if unit.startswith("k") else v


def _speed_text(s) -> tuple[float | None, str]:
    """'75-225 Mbps' -> (75, '75-225');  '1 Gbps' -> (1000, '1000');  '81.0 Mpbs' (sic) -> 81.

    For a range the LOW end is returned: it is what the plan reliably delivers, so it is the
    fair value to compare with the max speed an ISP reported at a location.
    """
    s = str(s or "")
    m = re.search(r"(\d[\d,.]*)\s*(?:-|–|to)?\s*(\d[\d,.]*)?\s*(g|m|k)", s, re.I)
    if not m:
        return None, ""
    vals = [_to_mbps(v.rstrip("."), m.group(3)) for v in (m.group(1), m.group(2)) if v]
    return min(vals), "-".join(f"{v:g}" for v in vals)


def _pairs(amounts, labels) -> str:
    a = [x.strip() for x in str(amounts or "").split(";")]
    lab = [x.strip() for x in str(labels or "").split(";")]
    out = [f"{lb or 'fee'}: ${am}" for am, lb in zip(a, lab + [""] * len(a)) if am and am not in ("0", "0.00")]
    return "; ".join(out)


def _from_fcc_spec(df: pd.DataFrame) -> pd.DataFrame:
    """Columns from the FCC data specification (unique_plan_identifier, network_technology_type, ...)."""
    g = lambda c: df[c] if c in df else pd.Series([""] * len(df), index=df.index)  # noqa: E731
    du, uu = g("bandwidth_download_units"), g("bandwidth_upload_units")
    rng = lambda lo, hi: [(f"{_num(a):g}" + (f"-{_num(b):g}" if _num(b) not in (None, _num(a)) else ""))  # noqa: E731
                          if _num(a) is not None else "" for a, b in zip(lo, hi)]
    return pd.DataFrame({
        "provider": g("provider_name"), "frn": g("fcc_registration_number").str.zfill(10),
        "upi": g("unique_plan_identifier"), "plan_name": g("service_plan_name"),
        "connection": g("connection_type").str.upper(),
        "technology": pd.to_numeric(g("network_technology_type"), errors="coerce"),
        "down_mbps": [_to_mbps(v, u) for v, u in zip(g("bandwidth_download_marketed_high"), du)],
        "up_mbps": [_to_mbps(v, u) for v, u in zip(g("bandwidth_upload_marketed_high"), uu)],
        "typical_down_mbps": rng(g("bandwidth_download_typical_low"), g("bandwidth_download_typical_high")),
        "typical_up_mbps": rng(g("bandwidth_upload_typical_low"), g("bandwidth_upload_typical_high")),
        "latency_ms": rng(g("latency_idle_low"), g("latency_idle_high")),
        "monthly_price": pd.to_numeric(g("price_recurring"), errors="coerce"),
        "intro_price": pd.to_numeric(g("fee_introductory"), errors="coerce"),
        "intro_period": g("fee_introductory_description"),
        "contract": [("Yes" if str(r).lower().startswith("y") else "No") + (f" ({d})" if d else "")
                     for r, d in zip(g("contract_required"), g("contract_details"))],
        "monthly_fees": [_pairs(a, b) for a, b in zip(g("fee_recurring"), g("fee_recurring_description"))],
        "one_time_fees": [_pairs(a, b) for a, b in zip(g("fee_one_time_amount"), g("fee_one_time_details"))],
        "early_termination_fee": g("fee_early_termination"), "data_included": g("policy_data_usage"),
        "segment": [_segment_from_name(n, pt) for n, pt in zip(g("service_plan_name"), g("price_type"))],
        "speed_basis": "marketed",
    })


_ATT_NAME_SPEED = re.compile(r"(?:internet(?:\s+basic)?|fiber)\s+(\d+(?:\.\d+)?)\s*(gig|g\b|m\b|k\b)?|(\d+)\s*gig|\b(\d{3,4})s\b", re.I)


def _att_marketed(name: str) -> float | None:
    """'Internet 300 (Fiber 300Mbps)' -> 300, 'Internet 2000 (Fiber 2GIG)' -> 2000, '1000S' -> 1000."""
    m = _ATT_NAME_SPEED.search(name or "")
    if not m:
        return None
    if m.group(3):
        return float(m.group(3)) * 1000
    if m.group(4):
        return float(m.group(4))
    v, unit = float(m.group(1)), (m.group(2) or "").lower()
    if unit.startswith("g"):
        return v * 1000
    if unit == "k" or (v >= 700 and "768" in name):
        return v / 1000
    return v


def _att_tech(name: str, down: float | None, up: float | None) -> float | None:
    n = (name or "").lower()
    if "internet air" in n:
        return 71.0
    if "fiber" in n:
        return 50.0
    if "copper" in n:
        return 10.0
    if down and up and down >= 5 and up >= 0.8 * down:   # symmetrical = fiber on AT&T's network
        return 50.0
    return None


def _att_segment(r) -> str:
    n = r["planName"].lower()
    if "erate" in n:
        return "E-Rate (schools/libraries)"
    if n.startswith("access from at&t"):
        return "low-income (Access from AT&T)"
    if "business" in n or "business.att.com" in r.get("customerSupportlinkText", "") \
            or "smb" in r.get("discountsBundlesLinks", ""):
        return "business"
    return "residential"


def _from_att(df: pd.DataFrame) -> pd.DataFrame:
    """AT&T's own column names (FCCPlanId, planName, speedsDownloadSpeed, ...)."""
    df = df.fillna("")
    rows = []
    for _, r in df.iterrows():
        td, td_txt = _speed_text(r["speedsDownloadSpeed"])
        tu, tu_txt = _speed_text(r["speedsUploadSpeed"])
        down = _att_marketed(r["planName"]) or td
        intro = not str(r["introductoryRateValue"]).strip().lower().startswith("is not")
        rows.append({
            "provider": r["providerName"], "frn": r["FCCPlanId"][1:11], "upi": r["FCCPlanId"],
            "plan_name": r["planName"], "connection": r["serviceType"].upper(),
            "technology": _att_tech(r["planName"], td, tu),
            "down_mbps": down, "up_mbps": tu,   # AT&T lists only typical upload; used for matching
            "typical_down_mbps": td_txt, "typical_up_mbps": tu_txt, "latency_ms": r["speedsLatency"],
            "monthly_price": _num(r["priceAmount"]),
            "intro_price": _num(r["priceAmount"]) if intro else None,
            "intro_period": (f"{r['introductoryPeriodLength']} then ${r['priceAfterIntroductory']}"
                             if intro else ""),
            "contract": r["contractStatus"], "monthly_fees": _pairs(r["monthlyFee"], r["monthlyFeeType"]),
            "one_time_fees": _pairs(r["oneTimeFee"], r["onTimeyFeeType"]),
            "early_termination_fee": r["terminationFee"], "data_included": r["dataMonthly"],
            "segment": _att_segment(r),
            "speed_basis": "marketed (from plan name)" if down != td else "typical",
        })
    return pd.DataFrame(rows)


def _segment_from_name(name: str, price_type: str = "") -> str:
    n = (name or "").lower()
    if re.search(r"essentials|\baccess\b|affordable|lifeline|\bacp\b", n):
        return "low-income program"
    if "pre-paid" in str(price_type).lower() or re.search(r"prepaid|^now\b", n):
        return "prepaid"
    if "upgrade" in n:
        return "upgrade add-on"
    if "business" in n:
        return "business"
    return "residential"


def _from_fcc_template(df: pd.DataFrame, file_name: str = "") -> pd.DataFrame:
    """The FCC's original label CSV template (unique_plan_id, service_plan_name, monthly_price,
    intro_rate_price, typical_download_speed, ...), often one file per plan (e.g. Antietam)."""
    df = df.fillna("").replace({"NULL": "", "N/A": ""})
    g = lambda c: df[c].astype(str).str.strip() if c in df else pd.Series([""] * len(df), index=df.index)  # noqa: E731
    src = g("source_file") if "source_file" in df else pd.Series([file_name] * len(df), index=df.index)
    names = g("service_plan_name").where(g("service_plan_name") != "", g("tier_plan_name"))
    intro = g("intro_rate").str.lower().str.startswith("y")
    return pd.DataFrame({
        "provider": g("provider_name"), "frn": g("unique_plan_id").str[1:11], "upi": g("unique_plan_id"),
        "plan_name": names, "connection": g("connection_type").str.upper(),
        "technology": [tech_from_name(n) or tech_from_name(f) for n, f in zip(names, src)],
        "down_mbps": [speed_from_name(n) or _num(d) for n, d in zip(names, g("typical_download_speed"))],
        "up_mbps": [_num(u) for u in g("typical_upload_speed")],
        "typical_down_mbps": g("typical_download_speed"), "typical_up_mbps": g("typical_upload_speed"),
        "latency_ms": g("typical_latency"),
        "monthly_price": [_num(x) for x in g("monthly_price")],
        "intro_price": [_num(x) if i else None for x, i in zip(g("intro_rate_price"), intro)],
        "intro_period": [t if i else "" for t, i in zip(g("intro_rate_time"), intro)],
        "contract": [("Yes" if c.lower().startswith("y") else "No") + (f" ({t})" if t else "")
                     for c, t in zip(g("contract_req"), g("contract_time"))],
        "monthly_fees": [_pairs(a, b) for a, b in zip(g("monthly_provider_fee"), g("monthly_provider_fee_descr"))],
        "one_time_fees": [_pairs(a, b) for a, b in zip(g("single_purchase_fees"), g("single_purchase_fee_descr"))],
        "early_termination_fee": g("early_termination_fee"), "data_included": g("monthly_data_allow"),
        "speed_basis": ["marketed (from plan name)" if speed_from_name(n) else "typical" for n in names],
    })


def _from_normalised(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for c in ("technology", "down_mbps", "up_mbps", "monthly_price", "intro_price"):
        out[c] = pd.to_numeric(out[c], errors="coerce")
    return out.fillna({c: "" for c in COLUMNS if c not in
                       ("technology", "down_mbps", "up_mbps", "monthly_price", "intro_price")})


def normalise(df: pd.DataFrame, isp: str, file_name: str = "") -> pd.DataFrame:
    """Any supported plan file -> COLUMNS."""
    df = df.rename(columns=lambda c: str(c).strip())
    cols = set(df.columns)
    if {"upi", "plan_name", "down_mbps", "segment"} <= cols:
        out = _from_normalised(df)
    elif "unique_plan_identifier" in cols:
        out = _from_fcc_spec(df.fillna(""))
    elif "FCCPlanId" in cols:
        out = _from_att(df)
    elif "unique_plan_id" in cols:
        out = _from_fcc_template(df, file_name)
    else:
        raise ValueError("Unrecognised plan file: expected FCC columns (unique_plan_identifier, ...) "
                         "or AT&T's (FCCPlanId, ...). Columns found: " + ", ".join(list(df.columns)[:12]))
    if "segment" not in out:
        out["segment"] = [_segment_from_name(n) for n in out["plan_name"]]
    if "speed_basis" not in out:
        out["speed_basis"] = "marketed"
    if "BUSINESS" in file_name.upper():
        out["segment"] = "business"
    out["isp"], out["catalog_file"] = isp, file_name
    return out[COLUMNS]


def load(isp: str) -> pd.DataFrame | None:
    """The ISP's downloaded file plus any plans built from its label pages (deduplicated by UPI)."""
    parts = [normalise(pd.read_csv(f, dtype=str, low_memory=False), isp, f.name)
             for f in (latest_file(isp), labels_file(isp), labels_file(isp, SITE_FILE)) if f]
    if not parts:
        return None
    return pd.concat(parts, ignore_index=True).drop_duplicates("upi", keep="first")


def load_all(isps: list[str] | None = None) -> dict[str, pd.DataFrame]:
    keys = isps or list(status()["isp"])
    out = {}
    for k in keys:
        df = load(k)
        if df is not None:
            out[k] = df
    return out


# ------------------------------------------------------------------------------ plan file from label pages
# For ISPs that publish labels only as web pages or PDFs (most small ISPs, and Verizon behind its
# address check): read the labels ONCE, store them as an FCC-format CSV, reuse for every address.
_NAME_TECH = [   # plan-name keywords -> NBM technology code
    (r"\bfios\b|fiber|fibre|\bftth\b|gpon", 50),
    (r"\bcable\b|docsis|coax", 40),
    (r"\bdsl\b|copper|adsl|vdsl", 10),
    (r"5g\s*home|lte\s*home|home\s*internet\s*(?:air|5g|lte)|internet\s*air|fixed\s*wireless|\bcbrs\b|\bwisp\b", 71),
]
_NAME_SPEED = re.compile(r"(\d+(?:\.\d+)?)\s*(gig|gbps|g\b|mbps|mb\b|m\b)", re.I)
TECH_CHOICES = {0: "detect from plan names", 50: "Fiber", 40: "Cable", 10: "Copper (DSL)",
                71: "Fixed wireless (licensed)", 70: "Fixed wireless (unlicensed)", 72: "Fixed wireless (LBR)"}


def tech_from_name(name: str) -> int | None:
    for pat, code in _NAME_TECH:
        if re.search(pat, name or "", re.I):
            return code
    return None


def speed_from_name(name: str) -> float | None:
    """'Fios 1 Gig' -> 1000, 'Internet 300 Mbps' -> 300."""
    m = _NAME_SPEED.search(name or "")
    if not m:
        return None
    v = float(m.group(1))
    return v * 1000 if m.group(2).lower().startswith("g") else v


def _low(rng: str) -> str:
    return (str(rng or "").split("-")[0]).strip()


def file_to_chunks(name: str, data: bytes) -> list[tuple[str, str]]:
    """Saved page / PDF / text -> [(source, text)] for the label parser."""
    if name.lower().endswith(".pdf") or data[:5] == b"%PDF-":
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data))
        return [(f"import:{name}", "\n".join((pg.extract_text() or "") for pg in reader.pages))]
    from .importer import file_to_chunks as html_chunks
    return html_chunks(name, data)


def labels_to_rows(chunks: list[tuple[str, str]], technology: int = 0,
                   provider_name: str = "") -> pd.DataFrame:
    """Broadband Facts labels in pages/PDFs -> rows in FCC data-spec columns."""
    from .label_parser import enrich_upis
    from .upi import find_upis_in_many

    rows = []
    for h in enrich_upis(find_upis_in_many(chunks), chunks):
        name = h.get("plan_name") or ""
        tech = tech_from_name(name) or (technology or None)
        down = speed_from_name(name) or _num(_low(h.get("typical_download_mbps")))
        intro = bool(h.get("intro_months"))
        rows.append({
            "date_published": date.today().isoformat(), "connection_type": "FIXED",
            "fcc_registration_number": h["frn"], "unique_plan_identifier": h["upi"],
            "network_technology_type": tech or "", "provider_name": provider_name,
            "service_plan_name": name or h["upi"],
            "bandwidth_download_units": "Mbps", "bandwidth_download_marketed_high": down or "",
            "bandwidth_upload_units": "Mbps",
            "bandwidth_upload_marketed_high": _num(_low(h.get("typical_upload_mbps"))) or "",
            "bandwidth_download_typical_low": _low(h.get("typical_download_mbps")),
            "bandwidth_download_typical_high": str(h.get("typical_download_mbps") or "").split("-")[-1],
            "bandwidth_upload_typical_low": _low(h.get("typical_upload_mbps")),
            "bandwidth_upload_typical_high": str(h.get("typical_upload_mbps") or "").split("-")[-1],
            "latency_idle_low": _low(h.get("typical_latency_ms")),
            "latency_idle_high": str(h.get("typical_latency_ms") or "").split("-")[-1],
            "price_type": "Post-Paid",
            "price_recurring": _num(h.get("price_after_intro")) if intro and h.get("price_after_intro")
            else _num(h.get("monthly_price")) or "",
            "fee_introductory": _num(h.get("monthly_price")) if intro else "",
            "fee_introductory_description": (f"Introductory price for {h['intro_months']} months" if intro else ""),
            "contract_required": "No" if re.search(r"none|no|not|month", str(h.get("contract", "")), re.I) else
            ("Yes" if h.get("contract") else ""),
            "contract_details": h.get("contract", ""),
            "fee_one_time_amount": "", "fee_one_time_details": h.get("one_time_fees", ""),
            "fee_recurring": "", "fee_recurring_description": h.get("provider_monthly_fees", ""),
            "fee_early_termination": h.get("early_termination_fee", ""),
            "policy_data_usage": h.get("data_included", ""),
        })
    return pd.DataFrame(rows)


def add_labels(isp: str, chunks: list[tuple[str, str]], technology: int = 0,
               provider_name: str = "") -> tuple[int, Path | None]:
    """Parse label pages and merge them into data/catalogs/<isp>/labels.csv. Returns (#plans, path)."""
    new = labels_to_rows(chunks, technology, provider_name)
    if new.empty:
        return 0, None
    path = _isp_dir(isp) / LABELS_FILE
    if path.exists():
        new = pd.concat([new, pd.read_csv(path, dtype=str)], ignore_index=True)
    new = new.drop_duplicates("unique_plan_identifier", keep="first")
    new.to_csv(path, index=False)
    return len(new), path


# ------------------------------------------------------------------------------ provider website
_LABEL_HINT = re.compile(r"broadband[\s_-]*(label|fact|disclos|nutrition)|/labels?\b|label|machine[\s_-]*readable", re.I)
CANDIDATE_PATHS = ["/broadband-labels", "/broadbandlabels", "/broadband-facts", "/broadband-label", "/labels",
                   "/internet/broadband-labels", "/residential/broadband-labels", "/legal/broadband-labels",
                   "/policies/broadband-labels", "/broadband-disclosures", "/disclosures", "/legal"]
MAX_FILES = 200


_ROBOTS: dict = {}


def _allowed(url: str) -> bool:
    """robots.txt check, fetched with our own User-Agent (cached per host).

    Missing robots.txt (404) = allowed; 401/403 on robots.txt = treated as disallowed.
    """
    import urllib.error
    from urllib.parse import urljoin, urlparse
    from urllib.robotparser import RobotFileParser
    host = urlparse(url).netloc
    if host not in _ROBOTS:
        rp = RobotFileParser()
        try:
            rp.parse(_get(urljoin(url, "/robots.txt")).decode("utf-8", "ignore").splitlines())
        except urllib.error.HTTPError as e:
            rp.disallow_all = e.code in (401, 403)
            rp.allow_all = not rp.disallow_all
        except Exception:  # noqa: BLE001 - unreachable robots.txt
            rp.allow_all = True
        _ROBOTS[host] = rp
    return _ROBOTS[host].can_fetch(USER_AGENT, url)


def _same_site(a: str, b: str) -> bool:
    from urllib.parse import urlparse
    host = lambda u: urlparse(u).netloc.lower().removeprefix("www.")  # noqa: E731
    return host(a) == host(b) or host(a).endswith("." + host(b)) or host(b).endswith("." + host(a))


def _page_signals(url: str, html: str) -> dict:
    """UPIs in the page text and links to label files (CSV / PDF) on the same site."""
    from urllib.parse import urljoin
    links = [urljoin(url, h) for h in re.findall(r'href=["\']([^"\'#]+)["\']', html, re.I)]
    links = [u for u in dict.fromkeys(links) if _same_site(u, url)]
    csvs = [u for u in links if re.search(r"\.csv(\?|$)", u, re.I)]
    pdfs = [u for u in links if re.search(r"\.pdf(\?|$)", u, re.I) and _LABEL_HINT.search(u + " label")
            and not re.search(r"eeo|privacy|terms|policy|annual|report", u, re.I)]
    pages = [u for u in links if _LABEL_HINT.search(u) and not re.search(r"\.(pdf|csv|jpe?g|png)(\?|$)", u, re.I)]
    upis = set(re.findall(r"(?<![A-Za-z0-9])F\d{10}[A-Za-z0-9]{1,15}(?![A-Za-z0-9])", html))
    return {"upis": upis, "csvs": csvs, "pdfs": pdfs, "pages": pages}


def find_label_page(url: str, log=print) -> tuple[str, str] | None:
    """From any page of the provider's site: the first page that holds labels or links to label files."""
    from urllib.parse import urljoin
    tried, queue = set(), [url]
    try:
        home = _get(url).decode("utf-8", "ignore")
        queue += _page_signals(url, home)["pages"][:10]
    except Exception as e:  # noqa: BLE001
        log(f"  homepage: {e}")
    queue += [urljoin(url, p) for p in CANDIDATE_PATHS]
    for u in queue:
        if u in tried:
            continue
        tried.add(u)
        if not _allowed(u):
            log(f"  {u}: disallowed by robots.txt, skipped")
            continue
        try:
            html = _get(u).decode("utf-8", "ignore")
        except Exception:  # noqa: BLE001 - 404 etc.
            continue
        sig = _page_signals(u, html)
        if sig["upis"] or sig["csvs"] or sig["pdfs"]:
            log(f"  labels page: {u} ({len(sig['upis'])} UPIs, {len(sig['csvs'])} CSV, {len(sig['pdfs'])} PDF)")
            return u, html
    return None


def crawl_site(isp: str, url: str, technology: int = 0, provider_name: str = "", log=print) -> tuple[int, str]:
    """Collect an ISP's plans from its public website ONCE (not per address).

    Finds the broadband-labels page, then reads every label CSV linked there (or the label PDFs,
    or labels embedded in the page). Saves data/catalogs/<isp>/site_plans.csv and remembers the URL
    so the plans are refreshed automatically. Respects robots.txt; one request at a time.
    """
    import time

    data = _get(url)
    head = data[:3000].decode("utf-8", "ignore").lower()
    if "unique_plan_id" in head or "fccplanid" in head:            # a direct CSV link
        frames, page = [normalise(pd.read_csv(io.BytesIO(data), dtype=str), isp, url)], url
    elif data[:5] == b"%PDF-":
        frames, page = [normalise(labels_to_rows(file_to_chunks(url, data), technology, provider_name),
                                  isp, url)], url
    else:
        found = find_label_page(url, log=log)
        if not found:
            raise ValueError("No broadband-labels page found on this site (it may build labels in the browser: "
                             "save the labels page with Cmd+S and upload it instead).")
        page, html = found
        sig, frames = _page_signals(page, html), []
        for u in sig["csvs"][:MAX_FILES]:
            try:
                frames.append(normalise(pd.read_csv(io.BytesIO(_get(u)), dtype=str), isp, u))
            except Exception as e:  # noqa: BLE001 - not a label CSV
                log(f"  skipped {u}: {str(e)[:80]}")
            time.sleep(0.3)
        if not frames:
            chunks = [(f"import:{page}", html)]
            for u in sig["pdfs"][:MAX_FILES]:
                try:
                    chunks += file_to_chunks(u, _get(u))
                except Exception as e:  # noqa: BLE001
                    log(f"  skipped {u}: {str(e)[:80]}")
                time.sleep(0.3)
            rows = labels_to_rows(chunks, technology, provider_name)
            if not rows.empty:
                frames.append(normalise(rows, isp, page))
    plans = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=COLUMNS)
    plans = plans[plans["upi"].astype(str).str.match(r"^F\d{10}")]
    if plans.empty:
        raise ValueError(f"Found {page} but no Unique Plan Identifiers in it or its label files.")
    if technology:
        plans["technology"] = plans["technology"].fillna(technology)
    if provider_name:
        plans["provider"] = plans["provider"].replace("", provider_name).str.strip()
    plans = plans.drop_duplicates("upi", keep="first")
    plans.to_csv(_isp_dir(isp) / SITE_FILE, index=False)
    (_isp_dir(isp) / SITE_URL_FILE).write_text(f"{url}\n{provider_name}\n")
    log(f"{isp}: {len(plans)} plans from {page}")
    return len(plans), page


def add_from_url(isp: str, url: str, technology: int = 0, provider_name: str = "") -> tuple[int, str]:
    return crawl_site(isp, url, technology, provider_name, log=lambda *_: None)


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")[:40] or "provider"


# ------------------------------------------------------------------------------ coverage
def coverage_report(providers: pd.DataFrame, catalogs: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """NBM providers (frn, brand_name, technologies, n_locations) + whether we have their plans."""
    if providers.empty:
        return providers
    rows = []
    for _, p in providers.iterrows():
        key, basis = _catalog_for({"frn": p["frn"], "brand_name": p["brand_name"]}, catalogs)
        rows.append({**p.to_dict(), "plan_file": key if basis else "",
                     "status": f"✅ {key} ({basis})" if basis else "❌ missing",
                     "suggested_key": key if key and not basis else slug(p["brand_name"])})
    return pd.DataFrame(rows)


# ------------------------------------------------------------------------------ matching
def _catalog_for(provider: dict, catalogs: dict[str, pd.DataFrame]) -> tuple[str | None, str]:
    frn = str(provider.get("frn") or "").zfill(10)
    for k, cat in catalogs.items():
        if "frns" not in cat.attrs:
            cat.attrs["frns"] = set(cat["frn"].unique())
        if frn and frn in cat.attrs["frns"]:
            return k, "FRN"
    k = match_isp(provider.get("brand_name"))
    return (k, "brand name") if k in catalogs else (k, "")


def match_location(providers: list[dict], catalogs: dict[str, pd.DataFrame], *,
                   residential_only: bool = True, skip_tech=(60, 61)) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Plans that fit each NBM provider row at one location.

    Returns (plans, coverage): plans has one row per catalog plan that matched; coverage has one
    row per NBM provider+technology saying whether a plan file exists and how many plans fit.
    """
    plans, coverage = [], []
    for p in providers:
        tech = int(p.get("technology") or 0)
        if tech in skip_tech:
            continue
        nbm_down, nbm_up = float(p.get("down") or 0), float(p.get("up") or 0)
        key, basis = _catalog_for(p, catalogs)
        cov = {"provider": p.get("brand_name"), "frn": p.get("frn"), "technology": tech,
               "nbm_max_down": nbm_down, "nbm_max_up": nbm_up, "isp": key or "", "matched_by": basis}
        if not basis:
            coverage.append({**cov, "plans": 0, "status": "no plan file for this ISP"})
            continue
        cat = catalogs[key]
        c = cat[cat["connection"].isin(["FIXED", ""])]
        if residential_only:
            c = c[c["segment"] == "residential"]
        compat = TECH_COMPAT.get(tech)
        if compat:
            # a plan with no technology in the file is assumed wired (fixed wireless plans are named as such)
            unknown_ok = c["technology"].isna() & (tech in (10, 40, 50))
            c = c[unknown_ok | c["technology"].isin(compat)]
        fits_down = c["down_mbps"].isna() | (c["down_mbps"] <= nbm_down * SPEED_TOLERANCE + 1)
        fits_up = c["up_mbps"].isna() | (c["up_mbps"] <= nbm_up * SPEED_TOLERANCE + 1)
        m, speed_rule = c[fits_down & fits_up], "download + upload"
        if m.empty and fits_down.any():                  # NBM upload may be outdated (mid-split upgrades)
            m, speed_rule = c[fits_down], "download only (no plan fit the NBM upload)"
        # Same tier offered with several marketed uploads (e.g. Xfinity 1 Gig x 35 / 100 / 1000 up,
        # depending on the network at the address): keep the fastest upload the location supports.
        mk = m["speed_basis"] == "marketed"
        if mk.any():
            best = m[mk].groupby(["plan_name", "down_mbps"], dropna=False)["up_mbps"].transform("max")
            m = pd.concat([m[mk][m[mk]["up_mbps"].fillna(-1) == best.fillna(-1)], m[~mk]])
        m = m.assign(
            nbm_provider=p.get("brand_name"), nbm_frn=p.get("frn"), nbm_technology=tech,
            nbm_max_down=nbm_down, nbm_max_up=nbm_up, matched_by=basis, speed_rule=speed_rule,
            technology_check=["matched" if pd.notna(t) else "not in file" for t in m["technology"]],
        )
        plans.append(m)
        coverage.append({**cov, "plans": len(m),
                         "status": f"{len(m)} plan(s) fit" if len(m) else "plan file loaded, but no plan fits"})
    allp = pd.concat(plans, ignore_index=True) if plans else pd.DataFrame(columns=COLUMNS)
    return allp, pd.DataFrame(coverage)


def summarise(plans: pd.DataFrame) -> pd.DataFrame:
    """Collapse regional price variants (Xfinity has thousands of UPIs per tier) to one row per tier."""
    if plans.empty:
        return plans
    keys = ["nbm_provider", "nbm_technology", "isp", "plan_name", "technology", "down_mbps", "up_mbps"]
    p = plans.copy()
    for k in keys:
        p[k] = p[k].fillna("")

    def rng(s):
        s = pd.to_numeric(s, errors="coerce").dropna()
        if s.empty:
            return ""
        return f"{s.min():g}" if s.min() == s.max() else f"{s.min():g}–{s.max():g}"

    def first(s):
        s = s[s.astype(str) != ""]
        return s.iloc[0] if len(s) else ""

    g = p.groupby(keys, sort=False).agg(
        monthly_price=("monthly_price", rng), intro_price=("intro_price", rng),
        intro_period=("intro_period", lambda s: "; ".join(sorted({x for x in s if x}))[:120]),
        contract=("contract", lambda s: "; ".join(sorted({x for x in s if x}))[:120]),
        typical_down_mbps=("typical_down_mbps", first), typical_up_mbps=("typical_up_mbps", first),
        latency_ms=("latency_ms", first), data_included=("data_included", first),
        one_time_fees=("one_time_fees", first), monthly_fees=("monthly_fees", first),
        upi=("upi", "min"), n_upis=("upi", "nunique"),
        technology_check=("technology_check", first), speed_rule=("speed_rule", first),
        matched_by=("matched_by", first),
    ).reset_index()
    return g.sort_values(["nbm_provider", "nbm_technology", "down_mbps"]).reset_index(drop=True)


def match_many(locations: pd.DataFrame, catalogs: dict[str, pd.DataFrame], *,
               residential_only: bool = True) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Plans for many locations (rows need location_id + providers_json, other columns are kept).

    Returns (plan tiers per location, coverage per location x provider).
    """
    import json

    keep = [c for c in ("grp", "block_geoid", "location_id", "address_primary", "city", "state", "zip")
            if c in locations.columns]
    plans, cov = [], []
    for _, r in locations.iterrows():
        provs = r["providers_json"]
        provs = json.loads(provs) if isinstance(provs, str) and provs else (provs or [])
        p, c = match_location(provs, catalogs, residential_only=residential_only)
        where = {k: r[k] for k in keep}
        if not p.empty:
            plans.append(summarise(p).assign(**where))
        if not c.empty:
            cov.append(c.assign(**where))
    cat = lambda xs: pd.concat(xs, ignore_index=True) if xs else pd.DataFrame()  # noqa: E731
    out_p, out_c = cat(plans), cat(cov)
    for df in (out_p, out_c):
        if not df.empty:
            df.insert(0, "location_id", df.pop("location_id"))
    return out_p, out_c
