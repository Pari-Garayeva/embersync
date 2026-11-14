"""
EmberSync — harmonisation core.

Pipeline
  1. static-source mask   : remove gas flares / industry / offshore using FIRMS 'type' from archive
                            products AND persistence, then propagate the mask to NRT products
                            that carry no 'type' column.
  2. common grid           : global regular lat/lon grid (default 0.1° ≈ 11 km) – one id space for
                            every sensor, every region, every year.
  3. pixel de-duplication  : one physical hotspot seen by 2-3 satellites the same local day counts once
                            (0.01° micro-grid ≈ VIIRS 375 m / MODIS 1 km footprint scale).
  4. cell-day fusion        : per cell & local solar day -> which platforms saw fire, FRP per sensor,
                            fused confidence (noisy-OR across independent platforms).
  5. cross-sensor agreement : same-cell and 8-neighbour tolerant matching (grid-edge effects).
  6. calibration           : (a) MODIS detection sensitivity vs fire size (VIIRS FRP)
                            (b) FRP cross-calibration MODIS -> VIIRS scale
                            (c) "backcast" model MODIS -> VIIRS-equivalent activity, validated
                                out-of-time (train Sep-Nov, test Dec-Mar). This is what makes a
                                consistent 2000->today record possible (VIIRS only exists since 2012).
"""
from __future__ import annotations

from dataclasses import dataclass, field
import numpy as np
import pandas as pd

MODIS_BITS = 1 | 2          # Terra | Aqua
VIIRS_BITS = 4 | 8 | 16     # S-NPP | NOAA-20 | NOAA-21


@dataclass
class Grid:
    res: float = 0.1

    @property
    def ncols(self) -> int:
        return int(round(360 / self.res))

    def cell_id(self, lat, lon):
        row = np.floor((90.0 - np.asarray(lat)) / self.res).astype(np.int64)
        col = np.floor((np.asarray(lon) + 180.0) / self.res).astype(np.int64)
        col = np.clip(col, 0, self.ncols - 1)
        return row * self.ncols + col

    def centre(self, cell):
        cell = np.asarray(cell, dtype=np.int64)
        row, col = cell // self.ncols, cell % self.ncols
        return 90.0 - (row + 0.5) * self.res, -180.0 + (col + 0.5) * self.res

    def neighbours(self, cell):
        """8-neighbour ids (n x 8)."""
        cell = np.asarray(cell, dtype=np.int64)[:, None]
        offs = np.array([-self.ncols - 1, -self.ncols, -self.ncols + 1, -1, 1,
                         self.ncols - 1, self.ncols, self.ncols + 1], dtype=np.int64)[None, :]
        return cell + offs


# ----------------------------------------------------------------------------------------------
# 1. static sources
# ----------------------------------------------------------------------------------------------
def static_source_mask(df: pd.DataFrame, res: float = 0.02, persist_frac: float = 0.5,
                       min_days: int = 20) -> np.ndarray:
    """Boolean array: True = keep (vegetation fire). Learns static micro-cells from archive 'type'
    (1 volcano, 2 static land, 3 offshore) and from persistence (burning on >= persist_frac of the days
    in the record – wildfires move, flares don't)."""
    g = Grid(res)
    mc = g.cell_id(df["lat"].values, df["lon"].values)
    static_cells = set(np.unique(mc[df["src_type"].isin([1, 2, 3]).values]).tolist())

    ndays = df["local_day"].nunique()
    per_cell_days = pd.Series(df["local_day"].values).groupby(mc).nunique()
    persistent = per_cell_days[(per_cell_days >= min_days) & (per_cell_days >= persist_frac * ndays)].index
    static_cells |= set(persistent.tolist())
    return ~np.isin(mc, np.fromiter(static_cells, dtype=np.int64)) if static_cells else np.ones(len(df), bool)


# ----------------------------------------------------------------------------------------------
# 2-4. fusion
# ----------------------------------------------------------------------------------------------
@dataclass
class HarmonisedResult:
    grid: Grid
    days: pd.DatetimeIndex
    cellday: pd.DataFrame            # one row per burning cell-day
    detections_in: int
    detections_static_removed: int
    unique_pixels: int
    calibration: dict = field(default_factory=dict)
    agreement: dict = field(default_factory=dict)


def fuse(df: pd.DataFrame, res: float = 0.1, min_conf: str = "l") -> HarmonisedResult:
    n_in = len(df)
    keep = static_source_mask(df)
    n_static = int((~keep).sum())
    df = df.loc[keep]
    if min_conf in ("n", "h"):
        allowed = ["n", "h"] if min_conf == "n" else ["h"]
        df = df[df["conf_class"].isin(allowed)]

    # local solar day can spill one day past the last UTC acquisition date -> partial day, drop it
    last_utc = df["t_utc"].dt.tz_localize(None).dt.normalize().max()
    first_utc = df["t_utc"].dt.tz_localize(None).dt.normalize().min()
    df = df[(df["local_day"] <= last_utc) & (df["local_day"] >= first_utc)]

    grid = Grid(res)
    days = pd.date_range(df["local_day"].min(), df["local_day"].max(), freq="D")
    day_idx = ((df["local_day"] - days[0]).dt.days).values.astype(np.int32)
    cell = grid.cell_id(df["lat"].values, df["lon"].values)
    micro = Grid(0.01).cell_id(df["lat"].values, df["lon"].values)

    is_v = (df["platform_bit"].values & VIIRS_BITS) > 0
    work = pd.DataFrame({
        "cell": cell, "day": day_idx, "micro": micro, "bit": df["platform_bit"].values,
        "is_v": is_v, "frp": df["frp"].values, "conf_p": df["conf_p"].values,
        "platform": df["platform"].values,
    })

    # unique physical hotspot pixels per day across all sensors
    uniq = work.drop_duplicates(["micro", "day"])
    unique_pixels = len(uniq)
    upix = uniq.groupby(["cell", "day"]).size().rename("n_pix")

    # fused confidence: noisy-OR across platforms of each platform's best detection
    best = work.groupby(["cell", "day", "platform"])["conf_p"].max()
    fused = (1 - (1 - best).groupby(level=[0, 1]).prod()).rename("q")

    # bitwise-OR of platform bits == sum of the distinct bits (vectorised)
    bits = work[["cell", "day", "bit"]].drop_duplicates().groupby(["cell", "day"])["bit"].sum().rename("bits")
    work["is_m"] = ~work["is_v"]
    g = work.groupby(["cell", "day"])
    out = pd.DataFrame({"bits": bits,
                        "n_det_m": g["is_m"].sum().astype(int),
                        "n_det_v": g["is_v"].sum().astype(int)})
    frp_m = work[~work.is_v].groupby(["cell", "day"])["frp"].sum().rename("frp_m")
    frp_v = work[work.is_v].groupby(["cell", "day"])["frp"].sum().rename("frp_v")
    out = out.join([frp_m, frp_v, fused, upix]).fillna({"frp_m": 0.0, "frp_v": 0.0, "n_pix": 0}).reset_index()
    out["n_pix"] = out["n_pix"].astype(np.int32)
    out["has_m"] = (out["bits"] & MODIS_BITS) > 0
    out["has_v"] = (out["bits"] & VIIRS_BITS) > 0
    b = out["bits"].values.astype(np.int64)
    out["n_platforms"] = sum(((b >> i) & 1) for i in range(5)).astype(np.int8)

    res_obj = HarmonisedResult(grid=grid, days=days, cellday=out, detections_in=n_in,
                               detections_static_removed=n_static, unique_pixels=unique_pixels)
    _agreement(res_obj)
    _calibrate(res_obj)
    return res_obj


# ----------------------------------------------------------------------------------------------
# 5. agreement
# ----------------------------------------------------------------------------------------------
def _key(cell, day):
    return np.asarray(day, dtype=np.int64) * (10 ** 9) + np.asarray(cell, dtype=np.int64)


def _agreement(r: HarmonisedResult) -> None:
    cd, grid = r.cellday, r.grid
    kv = _key(cd.loc[cd.has_v, "cell"], cd.loc[cd.has_v, "day"])
    km = _key(cd.loc[cd.has_m, "cell"], cd.loc[cd.has_m, "day"])

    def nb_hit(sub_mask, other_keys):
        sub = cd.loc[sub_mask]
        nbs = grid.neighbours(sub["cell"].values)
        keys = sub["day"].values.astype(np.int64)[:, None] * (10 ** 9) + nbs
        return np.isin(keys, other_keys).any(axis=1)

    cls = np.full(len(cd), "both", dtype=object)
    m_only = (cd.has_m & ~cd.has_v).values
    v_only = (~cd.has_m & cd.has_v).values
    cls[m_only] = np.where(nb_hit(m_only, kv), "both_nb", "modis_only")
    cls[v_only] = np.where(nb_hit(v_only, km), "both_nb", "viirs_only")
    cd["agree"] = cls

    n = len(cd)
    vc = pd.Series(cls).value_counts()
    r.agreement = {
        "cell_days": int(n),
        "both_same_cell": int(vc.get("both", 0)),
        "both_neighbour": int(vc.get("both_nb", 0)),
        "modis_only": int(vc.get("modis_only", 0)),
        "viirs_only": int(vc.get("viirs_only", 0)),
        "agreement_rate": round(float((vc.get("both", 0) + vc.get("both_nb", 0)) / max(n, 1)), 4),
        # constellation change: cell-days that exist ONLY because NOAA-20 was added
        "noaa20_only_cell_days": int((cd["bits"] == 8).sum()),
    }


# ----------------------------------------------------------------------------------------------
# 6. calibration
# ----------------------------------------------------------------------------------------------
def _design(m_cells, m_frp):
    return np.column_stack([np.ones_like(m_cells, dtype=float), np.log1p(m_cells), np.log1p(m_frp)])


def _fit_loglog(m_cells, m_frp, y):
    coef, *_ = np.linalg.lstsq(_design(m_cells, m_frp), np.log1p(y), rcond=None)
    return coef


def _pred_loglog(coef, m_cells, m_frp):
    return np.expm1(_design(m_cells, m_frp) @ coef)


def _r2(y, yhat):
    ss_res = np.sum((y - yhat) ** 2)
    ss_tot = np.sum((y - np.mean(y)) ** 2)
    return float(1 - ss_res / ss_tot) if ss_tot > 0 else float("nan")


def _calibrate(r: HarmonisedResult, train_frac: float = 0.42) -> None:
    cd = r.cellday
    # (a) detection sensitivity: P(MODIS sees it | VIIRS sees it) by VIIRS fire size
    v = cd[cd.has_v].copy()
    bins = [0, 5, 10, 25, 50, 100, 250, 500, 1e9]
    labels = ["<5", "5-10", "10-25", "25-50", "50-100", "100-250", "250-500", ">500"]
    v["frp_bin"] = pd.cut(v["frp_v"], bins=bins, labels=labels, right=False)
    v["m_hit"] = v["agree"].isin(["both", "both_nb"])
    sens = v.groupby("frp_bin", observed=False)["m_hit"].agg(["mean", "size"])
    sensitivity = [{"frp_bin_mw": str(k), "p_modis_detects": round(float(row["mean"]), 3), "n": int(row["size"])}
                   for k, row in sens.iterrows()]

    # (b) FRP cross-calibration on co-detected cell-days (same cell)
    both = cd[(cd.agree == "both") & (cd.frp_m > 0) & (cd.frp_v > 0)]
    lr = np.log(both["frp_v"].values) - np.log(both["frp_m"].values)
    frp_ratio_v_over_m = float(np.exp(np.median(lr))) if len(lr) else 1.0
    frp_corr = float(np.corrcoef(np.log(both["frp_m"]), np.log(both["frp_v"]))[0, 1]) if len(both) > 2 else float("nan")

    # harmonised FRP on VIIRS scale: VIIRS where available, otherwise calibrated MODIS
    cd["frp_h"] = np.where(cd.has_v, cd.frp_v, cd.frp_m * frp_ratio_v_over_m).astype("float32")

    # (c) backcast: VIIRS-equivalent active cells from MODIS only, on 1° tiles x day
    tile = (np.asarray(r.grid.centre(cd["cell"].values)[0]) // 1).astype(int) * 1000 + \
           (np.asarray(r.grid.centre(cd["cell"].values)[1]) // 1).astype(int)
    t = pd.DataFrame({"tile": tile, "day": cd["day"].values, "m": cd.has_m.values.astype(int),
                      "v": cd.has_v.values.astype(int), "mf": cd.frp_m.values}).groupby(["tile", "day"]).sum().reset_index()
    split_day = int(len(r.days) * train_frac)
    tr, te = t[t.day < split_day], t[t.day >= split_day]
    coef = _fit_loglog(tr.m.values.astype(float), tr.mf.values, tr.v.values.astype(float))
    te_hat = _pred_loglog(coef, te.m.values.astype(float), te.mf.values)

    # continental daily totals, out-of-time
    daily = t.groupby("day")[["m", "v"]].sum().reindex(range(len(r.days)), fill_value=0)
    tile_hat_all = _pred_loglog(coef, t.m.values.astype(float), t.mf.values)
    daily_hat = pd.Series(tile_hat_all).groupby(t.day.values).sum().reindex(range(len(r.days)), fill_value=0)
    te_days = daily.index >= split_day
    y, yh, raw = daily.v[te_days].values, daily_hat[te_days].values, daily.m[te_days].values
    mask = y > 20
    mape_model = float(np.mean(np.abs(yh[mask] - y[mask]) / y[mask]))
    mape_raw = float(np.mean(np.abs(raw[mask] - y[mask]) / y[mask]))

    r.calibration = {
        "modis_sensitivity_by_viirs_frp": sensitivity,
        "frp_ratio_viirs_over_modis": round(frp_ratio_v_over_m, 4),
        "frp_log_correlation": round(frp_corr, 4),
        "frp_pairs": int(len(both)),
        "backcast": {
            "model": "log1p(VIIRS_cells) = a + b*log1p(MODIS_cells) + c*log1p(MODIS_FRP), 1-degree tile x day",
            "a": round(float(coef[0]), 4), "b": round(float(coef[1]), 4), "c": round(float(coef[2]), 4),
            "train_days": f"{r.days[0].date()} .. {r.days[split_day - 1].date()}",
            "test_days": f"{r.days[split_day].date()} .. {r.days[-1].date()}",
            "tile_r2_test": round(_r2(np.log1p(te.v.values), np.log1p(te_hat)), 4),
            "daily_mape_test_model": round(mape_model, 4),
            "daily_mape_test_raw_modis": round(mape_raw, 4),
            "daily": {"modis": daily.m.tolist(), "viirs": daily.v.tolist(),
                      "backcast": [round(float(x), 1) for x in daily_hat.values]},
            "split_day": split_day,
        },
    }
