"""
EmberSync — burning-activity calendar + early-warning signals.

Cell-level flags (computed once, used by every region / bbox query)
  NEW_IGNITION  burning cell-day with NO burning in its 3x3 neighbourhood during the previous 5 days
  ESCALATING    cell burning 3 days in a row with strictly rising harmonised FRP

Region-level "surge" alert (any bbox, computed identically in the browser – see web/index.html)
  F(d)   = harmonised FRP (VIIRS scale, MW) over the region on day d; A(d) = active cells
  R7     = mean F over the last 7 days;  B28 = mean F over the 28 days before that
  ratio  = (R7 + 100) / (B28 + 100)          (+100 MW damps noise in quiet regions)
  level  : 0 Normal | 1 Elevated (ratio>=1.5) | 2 High (ratio>=2.5) | 3 Extreme (ratio>=4)
  gates  : level >0 needs >= MIN_ACTIVE active cells/day (7-day mean) AND an intensity of
           >= MIN_INTENSITY MW per active cell – this separates fast, intense fire fronts
           from extensive low-intensity savanna / agricultural burning.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .harmonize import HarmonisedResult, _key

NEW_IGNITION, ESCALATING = 1, 2
LOOKBACK_IGNITION = 5
THRESHOLDS = (1.5, 2.5, 4.0)
MIN_ACTIVE = 5
MIN_INTENSITY = 100.0  # MW per active cell (7-day mean)
DAMP = 100.0

# Approximate state / territory bounding boxes (lon_min, lat_min, lon_max, lat_max)
REGIONS = {
    "Australia (all)": (112.0, -44.0, 154.0, -9.0),
    "New South Wales": (141.0, -37.5, 153.7, -28.2),
    "Victoria": (140.9, -39.2, 150.0, -34.0),
    "Queensland": (138.0, -29.2, 153.6, -10.0),
    "Western Australia": (112.9, -35.2, 129.0, -13.7),
    "Northern Territory": (129.0, -26.0, 138.0, -10.9),
    "South Australia": (129.0, -38.1, 141.0, -26.0),
    "Tasmania": (143.8, -43.7, 148.5, -39.5),
    "Kangaroo Island": (136.5, -36.1, 138.2, -35.5),
    "NSW South Coast (Black Summer core)": (148.5, -37.6, 151.0, -34.5),
}


def cell_flags(r: HarmonisedResult) -> np.ndarray:
    cd, grid = r.cellday, r.grid
    flags = np.zeros(len(cd), dtype=np.int8)

    # new ignition
    cells, days = cd["cell"].values, cd["day"].values
    nb = np.concatenate([cells[:, None], grid.neighbours(cells)], axis=1)          # n x 9
    covered = [(_key(nb, days[:, None] + k)).ravel() for k in range(1, LOOKBACK_IGNITION + 1)]
    covered = np.unique(np.concatenate(covered))
    flags[~np.isin(_key(cells, days), covered)] |= NEW_IGNITION

    # escalation: 3 consecutive days, rising FRP
    s = cd.set_index(_key(cells, days))["frp_h"]
    f0 = s.values
    f1 = s.reindex(_key(cells, days - 1)).values
    f2 = s.reindex(_key(cells, days - 2)).values
    esc = (~np.isnan(f1)) & (~np.isnan(f2)) & (f0 > f1) & (f1 > f2)
    flags[esc] |= ESCALATING
    cd["flags"] = flags
    return flags


def region_series(r: HarmonisedResult, bbox) -> pd.DataFrame:
    cd = r.cellday
    lat, lon = r.grid.centre(cd["cell"].values)
    x0, y0, x1, y1 = bbox
    m = (lon >= x0) & (lon <= x1) & (lat >= y0) & (lat <= y1)
    sub = cd.loc[m]
    nd = len(r.days)
    idx = range(nd)
    g = sub.groupby("day")
    out = pd.DataFrame(index=idx)
    out["active"] = g.size().reindex(idx, fill_value=0)
    out["modis"] = g["has_m"].sum().reindex(idx, fill_value=0)
    out["viirs"] = g["has_v"].sum().reindex(idx, fill_value=0)
    out["both"] = g["agree"].apply(lambda a: a.isin(["both", "both_nb"]).sum()).reindex(idx, fill_value=0)
    out["frp_h"] = g["frp_h"].sum().reindex(idx, fill_value=0.0)
    out["new_ign"] = g["flags"].apply(lambda f: int(((f & NEW_IGNITION) > 0).sum())).reindex(idx, fill_value=0)
    out["pixels"] = g["n_pix"].sum().reindex(idx, fill_value=0)
    out["level"], out["ratio"] = alert_levels(out["active"].values, out["frp_h"].values)
    out.insert(0, "date", r.days)
    return out


def alert_levels(active: np.ndarray, frp: np.ndarray):
    a = pd.Series(active, dtype=float)
    f = pd.Series(frp, dtype=float)
    a7 = a.rolling(7, min_periods=7).mean()
    f7 = f.rolling(7, min_periods=7).mean()
    b28 = f.shift(7).rolling(28, min_periods=14).mean()
    ratio = ((f7 + DAMP) / (b28 + DAMP)).fillna(0).values
    intensity = (f7 / a7.where(a7 > 0)).fillna(0).values
    lvl = np.zeros(len(a), dtype=int)
    for i, th in enumerate(THRESHOLDS, start=1):
        lvl[ratio >= th] = i
    lvl[(a7.fillna(0).values < MIN_ACTIVE) | (intensity < MIN_INTENSITY)] = 0
    return lvl, np.round(ratio, 3)


def lead_time_report(r: HarmonisedResult) -> list[dict]:
    """For each region: date of peak harmonised FRP and how many days earlier the alert first hit High."""
    rows = []
    for name, bbox in REGIONS.items():
        s = region_series(r, bbox)
        if s["frp_h"].sum() == 0:
            continue
        peak = int(s["frp_h"].idxmax())
        window = s.loc[max(0, peak - 45):peak]
        hi = window[window.level >= 2]
        first_hi = int(hi.index[0]) if len(hi) else None
        rows.append({
            "region": name,
            "peak_date": str(s.loc[peak, "date"].date()),
            "peak_frp_gw": round(float(s.loc[peak, "frp_h"]) / 1000, 2),
            "first_high_alert": str(s.loc[first_hi, "date"].date()) if first_hi is not None else None,
            "lead_days": (peak - first_hi) if first_hi is not None else None,
            "active_cell_days": int(s["active"].sum()),
        })
    return rows
