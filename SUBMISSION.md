# Space Apps 2026 — submission text (copy into the project form)

**Project name:** EmberSync
**Challenge:** Harmonization of MODIS and VIIRS Hot Spots
**Project Demo (form field):** 30-second video `EmberSync_demo_30s.mp4`, uploaded as *unlisted* to YouTube or as a public Google Drive link → paste that URL. Alternative: the 7-slide deck exported to PDF / public link.
**Final Project (form field):** https://github.com/Pari-Garayeva/embersync
**Live demo (put inside the description + README):** https://pari-garayeva.github.io/embersync/ (GitHub Pages, serves `docs/index.html`)
_If the repo gets a different name, change `embersync` in both URLs._

---

## High-level summary (≈150 words)
Twenty-five years of satellite fire data exist, but they don't add up. MODIS (since 2000) and VIIRS (since 2012) see fire differently. In our Australian Black Summer test, MODIS caught only 28 % of the small fires VIIRS detected, 74 % of raw detections were duplicates from multiple satellites, and adding NOAA-20 alone created 7,448 "new" fire cell-days. EmberSync turns all MODIS and VIIRS FIRMS products into one de-duplicated, calibrated cell-day record. It then gives any area a burning-activity calendar, cross-sensor agreement, new-ignition and escalation flags, and an intensity-gated surge alert. A back-cast model reconstructs VIIRS-equivalent activity from MODIS alone, cutting daily error from 60 % to 26 % out-of-time. That makes a consistent 2000→today record possible. The surge alert reached "High" 28 days before New South Wales' peak fire day. The whole 2-million-detection season runs in 9 seconds.

## Project details
**What it does.** Users choose a region or draw a box on the map. EmberSync shows a daily calendar of harmonized burning activity, a stacked breakdown of what both sensors agree on and what only one sensor sees, a 4-level early-warning strip, and per-cell details: which satellites saw it, fused confidence, harmonized FRP, unique hotspots, and new-ignition or escalating status. The area's daily table exports as CSV.

**How it works.**
1. Adapters normalise every FIRMS CSV variant into one schema. The MODIS 0–100 confidence score is mapped to the VIIRS low/nominal/high classes.
2. Each detection is assigned to a *local solar day* (UTC + lon/15).
3. Static sources such as gas flares, industry, volcanoes and offshore platforms are learned from archive `type` and from persistence, then applied to NRT data, which has no `type` field.
4. A 0.01° micro-grid de-duplicates the same hotspot seen by several satellites.
5. Detections are fused on a global 0.1° grid with noisy-OR confidence and 8-neighbour tolerant cross-sensor matching.
6. Calibration produces three things: the MODIS detection-probability curve by fire size, the FRP cross-calibration (VIIRS/MODIS = 1.40×), and a log-linear back-cast of VIIRS-equivalent activity from MODIS cells and FRP.
7. Alerts compare 7-day harmonized FRP with a 28-day baseline. They are gated by fire intensity (≥ 100 MW per active cell), which keeps extensive low-intensity savanna burning from triggering false alarms.

**Benefits.** Emergency agencies get comparable seasons across the MODIS and VIIRS eras, and analysts get a record whose trends are not artefacts of a sensor change. Every transformation appears in a data-quality ledger. Validation runs on real data: out-of-time back-cast testing, alert lead times for 10 regions, and 100 % parity between the browser and Python alert logic.

**Tools.** Python (pandas, NumPy, PyArrow), D3.js, HTML Canvas, pytest, Playwright. The front end is a single static file, so it can be hosted for free and works offline.

**Scalability.** The global grid ids already work for any region or year. Next steps are daily ingestion from the FIRMS API into a partitioned lakehouse (Parquet or Delta), a `/calendar?bbox=` API, a 2000→today climatology baseline, and weather and fuel inputs for forecasting. Potential customers are fire agencies, insurers (parametric cover), carbon-credit verification and agricultural-burning compliance.

## NASA data used
- FIRMS MODIS Collection 6 active fires, Terra and Aqua (MCD14ML archive)
- FIRMS VIIRS 375 m active fires, Suomi-NPP (VNP14IMG archive)
- FIRMS VIIRS 375 m active fires, NOAA-20 (VJ114IMG NRT)
- (LANCE / EOSDIS); NASA Earthdata FIRMS documentation for the confidence and type definitions

## Other references
Natural Earth (basemap, via the `world-atlas` npm package); D3.js. The FIRMS CSVs used came from a public GitHub mirror of a FIRMS archive download (Australia, Sep 2019 – Mar 2020).

## Use of AI
An AI assistant (Claude) helped write code, tests and documentation. The team designed the approach, validated the outputs against the real NASA data and reviewed all of the results. No NASA logos or imagery were AI-generated.

---

## 7-slide demo outline (if a deck is required)
1. **Problem.** 25 years of fire data that don't add up: 28 % / 74 % / 7,448.
2. **EmberSync in one line.** One consistent fire record from every satellite (product screenshot).
3. **How it works.** The 6-step pipeline.
4. **Proof 1.** Sensitivity curve plus back-cast: 60 % → 26 % error.
5. **Proof 2.** Early warning: NSW High alert 28 days before the 4 Jan 2020 peak (calendar screenshot).
6. **Scale and business.** Global grid, FIRMS API, lakehouse; agencies, insurers, carbon markets.
7. **Team and ask.** Live demo link.
