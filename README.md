# Consumer Label Scraper

Measure ISP **competition at the census-block level** with the FCC National Broadband Map (NBM),
pick the **most and least competitive blocks**, randomly sample **addresses** there (via the
CostQuest Fabric v8), and collect each ISP's **Broadband Consumer Label – Unique Plan Identifier (UPI)**
by entering the address on the ISP's website.

Supported ISP sites: AT&T, Verizon, Spectrum, Xfinity, Frontier, Cox.

```
BigQuery (NBM + Fabric v8)                         ISP websites (Playwright browser)
┌────────────────────┐  ┌──────────────┐  ┌──────────────┐  ┌───────────────────────┐  ┌──────────┐
│1 block competition │→ │2 select most │→ │3 sample 10-20│→ │4 type address on each │→ │5 compare │
│  (# ISPs ≥100/20)  │  │  & least     │  │  locations + │  │  ISP site, open labels│  │  UPIs by │
│  per block_geoid   │  │  competitive │  │  addresses   │  │  regex the UPIs       │  │  group   │
└────────────────────┘  └──────────────┘  └──────────────┘  └───────────────────────┘  └──────────┘
 sql/01_...sql           sql/02_...sql     sql/03_...sql     src/clscraper/scrapers/     cls summary
```

## Project layout

| Path | What it does |
|---|---|
| `config.yaml` | Table names, states, speed threshold, competition definition, sample sizes, scraper settings |
| `sql/01_block_competition.sql` | Step 1: ISPs per block under 4 definitions → `block_competition` table |
| `sql/02_select_blocks.sql` | Step 2: N most / N least competitive blocks → `selected_blocks` table |
| `sql/03_sample_locations.sql` | Step 3: random locations, NBM ⨝ Fabric for addresses, + providers at each location |
| `src/clscraper/pipeline.py` | Runs steps 1–3 and turns the sample into `scrape_tasks.csv` |
| `src/clscraper/scrapers/base.py` | The browser engine (enter address → find plans → open labels → harvest) |
| `src/clscraper/scrapers/isps.py` | Per-ISP URL + selectors (**the part you will tune**) |
| `src/clscraper/upi.py` | Finds UPIs (`F` + 10-digit FRN + ≤15 alphanumerics) in text/HTML/JSON |
| `src/clscraper/cli.py` | `cls` command line |
| `app.py` | Streamlit web app (enter a location → get UPIs) |
| `outputs/` | CSVs, screenshots and HTML of every lookup |

## Competition definitions

A provider counts if it advertises **≥ `min_down`/`min_up` Mbps** (default 100/20) at a location.
A block is **competitive** if it has **≥ `competitive_threshold`** (default 2, i.e. "more than one") such ISPs.

| `definition` | Technology codes counted |
|---|---|
| `terrestrial` | everything except satellite (60 GSO, 61 NGSO/LEO) |
| `wired` | 10 copper, 40 cable, 50 fiber |
| `fiber_fw` | 50 fiber, 70/71/72 fixed wireless |
| `fiber` | 50 fiber only (the "more than one fiber company" version) |

All four are computed in one pass. `competition.definition` only chooses which one is used to pick blocks.
Per block you get `n_isp_<def>` (distinct ISPs anywhere in the block), `avg_isp_per_bsl_<def>`
and `share_bsl_competitive_<def>` (location-level measures, because an ISP may serve only part of a block).

## The web app (Broadband Plan Finder)

```bash
cls build-index        # one time: address lookup table for app.index_states (MD: ~0.7 GB scanned)
streamlit run app.py   # opens http://localhost:8501
```

Fully automatic: no ISP website is opened per address.

1. **Type an address.** It is matched to a Fabric location (ZIP + house number, fuzzy-matched street).
   The page shows the FCC map's providers there: technology, max speeds and FRN, plus whether each one has a plan file.
2. **Plans at this address.** For each provider, the app takes the ISP's plan file (see below) and keeps the
   plans that match it on:
   - **ISP:** FRN, or brand name if the FRN isn't in the file
   - **technology:** fiber / cable / copper / fixed wireless
   - **speed:** at or below the max advertised speed the ISP reported for this location; upload is checked too,
     which picks Xfinity's right variant (35 / 100 / 1000 up)

   By default only residential plans are shown; a toggle adds low-income, prepaid and business plans.
   You can download one row per plan tier, or every matching Unique Plan Identifier.
3. **🗂️ ISP plan files tab.** Lists every provider in the FCC map for the indexed states, biggest first,
   with whether it has a plan file and the share of provider-locations covered. Add a missing ISP **once**
   from a link (CSV, labels page or PDF), from files (CSV, PDF, page saved with ⌘S) or from pasted label text.
4. **🧪 Research sample tab.** Plans for every location sampled by `cls pipeline`, saved to `outputs/sample_plans.csv`.

### Making it work for every ISP, at any address

* **Data source:** `app.location_table` in config.yaml points at a location table with every NBM release nested
  (`ck3378.combined_location_data`: Maryland, Fabric v7, releases 2022_06–2025_12). `app.period` picks the release
  (default 2025_12, the newest, since the plan files are current). Leave `location_table` empty to build from
  `nbm_table` + `fabric_table` instead. Run `cls build-index` again after changing either setting.
* **Any address:** set `app.index_states` in config.yaml (`[]` = all states) and run `cls build-index` once.
  The national build scans about 60 GB once (about $0.40). After that, each lookup reads a few MB, because the
  table is clustered by ZIP.
* **Every ISP:** run `cls coverage` (or use the tab) to get the to-do list. Each provider needs a plan file once:
  1. **Downloadable CSV:** add its URL to `catalogs.yaml` and it is refreshed automatically.
  2. **Labels page / PDF:** give the link or the file in the tab. The labels are parsed into
     `data/catalogs/<isp>/labels.csv`.
  3. **Labels only in the browser** (Verizon, T-Mobile): open the labels page in your normal browser, save it
     (⌘S), upload. National-price plans (Verizon 5G/LTE Home, T-Mobile Home Internet) are then covered everywhere.
  4. **Nothing online:** ask the provider for its machine-readable file. It is required by 47 CFR 8.1(a)(3).

### Plan files (machine-readable labels)

FCC rule 47 CFR 8.1(a)(3) requires each ISP to publish all its current labels as one machine-readable file.
[catalogs.yaml](catalogs.yaml) lists where each one is. The app downloads each file **once per ISP**
(not once per address) into `data/catalogs/<isp>/` and refreshes it weekly.

| ISP | File | Notes |
|---|---|---|
| AT&T | `att.com/scmsassets/nutritionlabels/csv/csvfile/BBFMachineReadable.csv` | ~350 plans (fixed + mobile); AT&T's own column names |
| Xfinity | `xfinity.com/.well-known/broadband-labels/<date>-0003768165-USA-RESIDENTIAL.csv` | FCC column format; ~98k rows (regional price variants) |
| Verizon, Spectrum | not found yet | download in your browser, then *sidebar → Plan files → Add a plan file* |

Any file in the FCC column format (`unique_plan_identifier`, `network_technology_type`, ...) works, for any ISP.

```bash
cls catalogs --refresh                         # download all plan files now
cls plans --street "100 Holliday St" --zip 21202   # plans at a location, saved to outputs/
```

**Limits.** The files have no geography. A match means *the plan is consistent with what the ISP reported
to the FCC map for this location*, not that the ISP's address check confirmed it. Where an ISP prices by
region (Xfinity), one tier has many UPIs, so prices show as a range. Check a sample of addresses
with step 3 to measure accuracy.

Label fields are read by [label_parser.py](src/clscraper/label_parser.py) from the FCC's fixed label captions.
Each search is also appended to `outputs/lookup_results.csv`.

## Setup (one time)

```bash
cd "Consumer Label Scrapper"
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt && pip install -e .
playwright install chromium

# BigQuery login (pick one)
gcloud auth application-default login          # personal account, or
cp .env.example .env   # and set GOOGLE_APPLICATION_CREDENTIALS to a service-account key
```

Edit `config.yaml`: `bigquery.project`, `nbm_table`, `fabric_table`, `output_dataset`, and `scope.states`.
**Start with one state.** A nationwide NBM scan is hundreds of GB.

## Running it, step by step

```bash
cls check        # 0. are the tables reachable and do the column names match?
cls competition  # 1. builds <output_dataset>.block_competition; prints #blocks by #ISPs
cls select       # 2. builds <output_dataset>.selected_blocks (+ outputs/selected_blocks.csv)
cls sample       # 3. outputs/sample_locations.csv and outputs/scrape_tasks.csv
                 #    (or `cls pipeline` = steps 1–3)
cls scrape --limit 3            # 4. try a few lookups first, watch the browser
cls scrape --isp spectrum --manual   # finish in the browser when automation fails
cls scrape                      # full run (resumable: finished lookups are skipped)
cls summary                     # 5. status and #distinct UPIs by group x ISP
```

Single address, without BigQuery:

```bash
cls lookup --address "123 Main St, Durham, NC 27701" --isp spectrum att
cls lookup --location-id 1234567890      # pulls address + ISPs from Fabric/NBM
streamlit run app.py                     # the same, in a web UI
```

### Output: `outputs/scrape_results.csv`

One row per (location, ISP, UPI): `grp` (most/least competitive), `block_geoid`, `location_id`,
`isp`, `status`, `upi`, `frn`, `plan_code`, `frn_matches_nbm`, `context` (text around the UPI,
usually plan name and price), `screenshot`, `html`.

`status` values:

| status | meaning |
|---|---|
| `ok` | at least one UPI was found |
| `no_upi_found` | plans page loaded but no UPI was found (labels may need a click the selectors miss) |
| `not_serviceable` | the ISP says it doesn't serve this address (compare with what the NBM claims) |
| `address_issue` | the site couldn't match the Fabric address |
| `blocked` | bot check / access denied. Slow down, run headed, use `--manual` |
| `timeout` / `error` | see `message` and the screenshot |

## Tuning an ISP scraper (expect to do this)

ISP websites change often and are A/B tested, so the selectors in `isps.py` are only starting points.
To fix one:

```bash
playwright codegen https://www.spectrum.com/address/localization
```

A browser opens with a recorder. Type a real address, pick the suggestion, click submit, and open a
"Broadband Facts" label. The recorder shows the selectors it used, e.g. `get_by_placeholder("Street Address")`
or `#address1`. Put the CSS ones at the front of the matching list in `isps.py`
(`address_input_selectors`, `suggestion_selectors`, `submit_selectors`, `open_form_selectors`, …).
If a site needs extra clicks (for example "Is this your home or business?"), override `enter_address()`
in that ISP's class.

Tips:
* Look at `outputs/artifacts/<isp>/*.png` to see where a lookup stopped.
* The UPI is often already in the page's hidden HTML or in a JSON API response.
  The engine searches the visible text, the full HTML, iframes, shadow DOM **and** network responses.
* `frn_matches_nbm` = the FRN inside the UPI matches the FRN the NBM lists for that ISP at that location.
  This is a useful sanity check.
* Providers must also publish labels in a **machine-readable format**. For an ISP whose site resists
  automation, that file can show which UPIs exist. The address lookup is still what tells you which
  plans are offered at a specific location.

## Responsible scraping

* Low volume only (tens of addresses), with random 8–20 s pauses (`delay_between_requests_s`).
* No CAPTCHA solving or bot-detection evasion. If a site blocks you, use `--manual` and do it by hand.
* Check each site's terms of use, and with your advisor or IRB whether institutional approval is needed.
* Fabric data is licensed. Don't publish raw addresses. Share block-level or `location_id`-level results.

## Data notes

* `fcc_bdc.nbm_hive` is an external Parquet table on `gs://fcc_nbm/hive/`, partitioned by
  `period` (e.g. `2025_12`) and `state` (full name). The code adds literal `period`/`state` filters
  (`bigquery.nbm_period` + `scope.states`), so one state is about 1 GB instead of hundreds.
  BigQuery's dry-run estimate does **not** include external tables. The `max_gb_billed` cap still applies.
* NBM and Fabric must be from the same vintage. NBM `location_id`s join to the Fabric version the map was built on (v8 here).
* `business_residential_code` R/X = residential and both. Fabric `building_type_code` R/X is used for sampling.
* If your BigQuery column names differ, `cls check` lists what's missing. The simplest fix is a
  BigQuery VIEW that renames columns, with `config.yaml` pointing at the view.
* After the Verizon–Frontier and Charter–Cox deals, some ISP sites may redirect or merge.
  The FRN in the UPI shows which legal entity issued the label.

## Tests

```bash
pytest -q
```
