"""
EmberSync — end-to-end pipeline / CLI.

    python -m embersync.pipeline --inputs data/raw/*.csv --res 0.1 --out data/out

Outputs
  data/out/cellday.parquet   harmonised cell-day table (the product's core asset / API backend)
  data/out/cellday.csv       same, CSV
  data/out/report.json       QA, calibration, agreement, lead-time validation
  data/out/web_payload.json  compact columnar payload consumed by web/index.html
"""
from __future__ import annotations

import argparse
import base64
import glob
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd

from .ingest import load_many, PLATFORM_BITS
from .harmonize import fuse
from .warning import cell_flags, lead_time_report, region_series, REGIONS


def _b64(arr: np.ndarray) -> str:
    return base64.b64encode(np.ascontiguousarray(arr).tobytes()).decode()


def build_web_payload(r, raw_counts: dict, report: dict) -> dict:
    cd = r.cellday.sort_values(["day", "cell"]).reset_index(drop=True)
    cells, inv = np.unique(cd["cell"].values, return_inverse=True)
    lat, lon = r.grid.centre(cells)
    agree_code = cd["agree"].map({"both": 0, "both_nb": 1, "modis_only": 2, "viirs_only": 3}).values
    return {
        "meta": {
            "product": "EmberSync", "grid_res_deg": r.grid.res,
            "start": str(r.days[0].date()), "ndays": len(r.days),
            "platform_bits": PLATFORM_BITS, "raw_counts": raw_counts,
            "regions": REGIONS,
        },
        "cells": {"lat": _b64(lat.astype(np.float32)), "lon": _b64(lon.astype(np.float32)), "n": int(len(cells))},
        "cd": {   # columnar, little-endian typed arrays
            "n": int(len(cd)),
            "cell": _b64(inv.astype(np.uint32)),
            "day": _b64(cd["day"].values.astype(np.uint16)),
            "bits": _b64(cd["bits"].values.astype(np.uint8)),
            "agree": _b64(agree_code.astype(np.uint8)),
            "flags": _b64(cd["flags"].values.astype(np.uint8)),
            "q": _b64(np.round(cd["q"].values * 100).astype(np.uint8)),
            "frp": _b64(cd["frp_h"].values.astype(np.float32)),
            "pix": _b64(np.minimum(cd["n_pix"].values, 65535).astype(np.uint16)),
        },
        "report": report,
    }


def run(inputs: list[str], res: float, out: str, min_conf: str = "l") -> dict:
    t0 = time.time()
    outp = Path(out)
    outp.mkdir(parents=True, exist_ok=True)

    df = load_many(inputs)
    raw_counts = df["platform"].value_counts().to_dict()
    coverage = df.assign(utc_day=df["t_utc"].dt.date).groupby("platform")["utc_day"].agg(["min", "max"]).astype(str).to_dict("index")
    t_ingest = time.time() - t0

    r = fuse(df, res=res, min_conf=min_conf)
    cell_flags(r)
    t_fuse = time.time() - t0 - t_ingest

    cd = r.cellday
    lat, lon = r.grid.centre(cd["cell"].values)
    export = cd.assign(lat=lat, lon=lon, date=r.days[cd["day"].values].date)
    export.to_parquet(outp / "cellday.parquet", index=False)
    export.drop(columns=["bits"]).to_csv(outp / "cellday.csv", index=False)

    national = region_series(r, REGIONS["Australia (all)"])
    report = {
        "run": {"inputs": [Path(p).name for p in inputs], "grid_res_deg": res, "min_conf": min_conf,
                "seconds_ingest": round(t_ingest, 1), "seconds_fuse": round(t_fuse, 1)},
        "volume": {
            "detections_in": r.detections_in,
            "static_source_removed": r.detections_static_removed,
            "unique_hotspot_pixels": r.unique_pixels,
            "duplicate_reduction_pct": round(100 * (1 - r.unique_pixels / max(r.detections_in - r.detections_static_removed, 1)), 1),
            "burning_cell_days": int(len(cd)),
            "platform_counts": raw_counts,
            "platform_coverage": coverage,
        },
        "agreement": r.agreement,
        "calibration": r.calibration,
        "flags": {"new_ignitions": int(((cd["flags"] & 1) > 0).sum()), "escalating": int(((cd["flags"] & 2) > 0).sum())},
        "lead_time": lead_time_report(r),
        "national_peak": {
            "date": str(national.loc[national.frp_h.idxmax(), "date"].date()),
            "active_cells": int(national.active.max()),
            "frp_gw": round(float(national.frp_h.max()) / 1000, 2),
        },
    }
    (outp / "report.json").write_text(json.dumps(report, indent=2, default=str))
    payload = build_web_payload(r, raw_counts, report)
    (outp / "web_payload.json").write_text(json.dumps(payload, default=str))
    report["run"]["seconds_total"] = round(time.time() - t0, 1)
    return report


def main():
    ap = argparse.ArgumentParser(description="EmberSync MODIS+VIIRS hotspot harmonisation")
    ap.add_argument("--inputs", nargs="+", default=sorted(glob.glob("data/raw/*.csv")))
    ap.add_argument("--res", type=float, default=0.1)
    ap.add_argument("--min-conf", default="l", choices=["l", "n", "h"])
    ap.add_argument("--out", default="data/out")
    a = ap.parse_args()
    rep = run(a.inputs, a.res, a.out, a.min_conf)
    print(json.dumps({k: rep[k] for k in ("run", "volume", "agreement", "flags", "national_peak")}, indent=2, default=str))
    print(json.dumps(rep["lead_time"], indent=2))
    print(json.dumps({k: v for k, v in rep["calibration"].items() if k != "backcast"}, indent=2))
    print(json.dumps({k: v for k, v in rep["calibration"]["backcast"].items() if k != "daily"}, indent=2))


if __name__ == "__main__":
    main()
