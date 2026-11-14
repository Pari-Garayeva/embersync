# 🔥 EmberSync — one consistent fire record from every satellite

**NASA Space Apps Challenge 2026 · Challenge: _Harmonization of MODIS and VIIRS Hot Spots_**

EmberSync merges NASA FIRMS active-fire detections from **MODIS (Terra, Aqua)** and **VIIRS (S-NPP, NOAA-20, NOAA-21)** into a single de-duplicated, calibrated **cell-day burning-activity record**. For any area it produces a **burning-activity calendar**, **cross-sensor agreement**, **new-ignition / escalation flags** and a **surge early-warning level**.

> Demo: Australia's Black Summer (1 Sep 2019 – 31 Mar 2020), **2,057,079** raw detections from 4 satellites, processed in **~9 s** on a laptop.

## Why
- MODIS (2000→) and VIIRS (2012→) disagree. In our data **MODIS catches only 28 % of the small fires (<5 MW) that VIIRS sees**, but 99 % of large ones (>500 MW). A naive MODIS→VIIRS time series shows "more fire" that is really just a better sensor.
- The same hotspot is reported by 2–4 satellites: **74 % of raw detections are duplicates**.
- When a new satellite joins (NOAA-20 on 4 Dec 2019 here), the record jumps: **7,448 cell-days exist only because of NOAA-20**.
- Night overpasses fall on a different UTC date than the afternoon fire they belong to.

## What it does (pipeline)
| # | Step | Detail |
|---|------|--------|
| 1 | Ingest & normalise | Any FIRMS CSV (MODIS C6/C6.1, VIIRS archive/NRT) → one schema. MODIS 0–100 confidence → l/n/h (FIRMS rule). Footprint = scan × track. |
| 2 | Local solar day | `UTC + lon/15 h`, so a 01:00 night pass belongs to the same fire-day. |
| 3 | Static-source mask | Flares, industry, volcanoes, offshore are learned from archive `type` + persistence, then propagated to NRT data that has no `type`. |
| 4 | De-duplicate | 0.01° micro-grid: one physical hotspot per day across all satellites. |
| 5 | Fuse & calibrate | 0.1° cell-day fusion; noisy-OR fused confidence; 8-neighbour tolerant agreement; FRP cross-calibration (VIIRS/MODIS = **1.40×**); MODIS sensitivity curve; **back-cast model** MODIS → VIIRS-equivalent activity. |
| 6 | Calendar & alerts | Any bbox → daily calendar, new ignitions, escalating cells, intensity-gated **surge alert** (7-day FRP vs 28-day baseline). |

## Validation (real data, this repo)
| Test | Result |
|---|---|
| Back-cast, out-of-time (train Sep–Nov, test Dec–Mar) | national daily error **60 % → 26 %** vs raw MODIS; tile-day R² 0.49 |
| Early warning: first High alert before peak-FRP day | NSW **28 d**, Victoria 40 d, NSW South Coast 35 d, Kangaroo Island 11 d, SA 14 d |
| Cross-sensor agreement | 63 % of cell-days seen by both (same or neighbour cell); 34 % VIIRS-only; 3 % MODIS-only |
| Browser ↔ Python alert parity | 100 % identical levels (e2e test) |

## Run it
```bash
pip install -r requirements.txt
# put FIRMS CSVs in data/raw/  (https://firms.modaps.eosdis.nasa.gov/download/ – any region/period, MODIS + VIIRS)
python -m embersync.pipeline --inputs data/raw/*.csv --res 0.1 --out data/out
python scripts/build_web.py          # -> web/dist/index.html (hosted) + index_offline.html (no internet)
python -m pytest tests/test_core.py  # unit tests (synthetic, <1 s)
python tests/e2e_browser.py          # headless browser test (needs playwright + chrome)
```
Outputs: `data/out/cellday.parquet|csv` (harmonised record, the API backend), `report.json` (QA, calibration, validation), `web_payload.json`.

Demo data used: FIRMS archive CSVs `fire_archive_M6_155743.csv` (MODIS), `fire_archive_V1_155745.csv` (VIIRS S-NPP), `fire_nrt_J1V-C2_155744.csv` (VIIRS NOAA-20), Australia 2019-09-01 → 2020-03-31.

## Repo layout
```
embersync/ingest.py     FIRMS adapters -> canonical schema (add a sensor = add an alias)
embersync/harmonize.py  grid, static mask, de-dup, fusion, agreement, calibration, back-cast
embersync/warning.py    cell flags, region series, surge alert, lead-time validation
embersync/pipeline.py   CLI + exports
web/template.html       dashboard (D3, canvas map, calendar, charts); build_web.py bundles data
tests/                  unit + end-to-end browser tests
```

## Scalability & roadmap
- **Global grid ids** already (0.1° row/col over the whole Earth); swap to H3 for multi-resolution.
- Daily **FIRMS API** ingestion (Airflow/cron) → Parquet/Delta Lake partitioned by date → DuckDB/Spark for continent-scale runs; the browser does bbox aggregation client-side today, a FastAPI `/calendar?bbox=` endpoint is the next step.
- Full **2000→today climatology** using the back-cast, so alerts compare against "normal for this place and week", not just the last 28 days.
- Add weather/fuel (FWI, soil moisture) to turn the surge alert into a forecast.

## Who pays (startup angle)
Emergency & forestry agencies (consistent seasonal reports), **insurers / parametric cover** (stable 20-year fire-frequency baselines), **carbon-credit verification** (forest projects need a consistent fire history across sensor eras), agriculture-burning compliance.

## Credits & data
NASA FIRMS / LANCE (EOSDIS): MODIS C6 MCD14ML, VIIRS 375 m VNP14IMG / VJ114IMG. Basemap: Natural Earth via `world-atlas`. Charts: D3.js.
Not an official NASA product. Region boxes are approximate bounding boxes. Licence: MIT.
