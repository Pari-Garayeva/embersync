"""Unit tests for EmberSync core logic (synthetic data – runs in < 2 s, no NASA download needed)."""
import numpy as np
import pandas as pd
import pytest

from embersync.ingest import normalise, modis_conf_to_class
from embersync.harmonize import Grid, fuse, static_source_mask
from embersync.warning import alert_levels, cell_flags, region_series, NEW_IGNITION, ESCALATING


def _modis(rows):
    return pd.DataFrame([dict(latitude=la, longitude=lo, brightness=330, scan=1.0, track=1.0, acq_date=d, acq_time=t,
                              satellite=s, instrument="MODIS", confidence=c, version="6.03", bright_t31=290,
                              frp=f, daynight="D", type=ty) for la, lo, d, t, s, c, f, ty in rows])


def _viirs(rows, nrt=False):
    out = []
    for la, lo, d, t, s, c, f in rows:
        r = dict(latitude=la, longitude=lo, bright_ti4=340, scan=0.4, track=0.4, acq_date=d, acq_time=t, satellite=s,
                 instrument="VIIRS", confidence=c, version="2.0NRT" if nrt else "1", bright_ti5=295, frp=f, daynight="D")
        if not nrt:
            r["type"] = 0
        out.append(r)
    return pd.DataFrame(out)


def test_modis_confidence_rule():
    assert modis_conf_to_class(pd.Series([0, 29, 30, 79, 80, 100])).tolist() == ["l", "l", "n", "n", "h", "h"]


def test_platform_aliases_and_schema():
    m = normalise(_modis([(-33.0, 150.0, "2019-12-01", "14", "Terra", "85", 50.0, 0)]))
    v = normalise(_viirs([(-33.0, 150.0, "2019-12-01", "0300", "1", "h", 20.0)], nrt=True))
    assert m.platform[0] == "Terra" and m.conf_class[0] == "h" and m.t_utc[0].minute == 14
    assert v.platform[0] == "NOAA-20" and v.sensor[0] == "VIIRS" and v.src_type[0] == -1 and v["product"][0] == "nrt"


def test_local_solar_day_shifts_night_overpass():
    # 15:00 UTC at lon 150 E = 01:00 next local day
    v = normalise(_viirs([(-33.0, 150.0, "2019-12-01", "1500", "N", "n", 5.0)]))
    assert str(v.local_day[0].date()) == "2019-12-02"


def test_grid_roundtrip_and_neighbours():
    g = Grid(0.1)
    c = g.cell_id([-33.05], [150.05])[0]
    lat, lon = g.centre([c])
    assert abs(lat[0] + 33.05) < 1e-9 and abs(lon[0] - 150.05) < 1e-9
    assert g.neighbours([c]).shape == (1, 8)


def _scenario():
    days = pd.date_range("2019-11-01", periods=15)  # < persistence min_days (20).strftime("%Y-%m-%d")
    m_rows, v_rows = [], []
    for i, d in enumerate(days):
        # big fire seen by both, growing FRP
        m_rows.append((-34.97, 149.03, d, "0030", "Aqua", "90", 100.0 + 10 * i, 0))
        v_rows.append((-34.97, 149.03, d, "0300", "N", "h", 120.0 + 10 * i))
        v_rows.append((-34.972, 149.032, d, "0300", "1", "h", 110.0))   # NOAA-20 duplicate of same hotspot
        # small fire only VIIRS sees
        v_rows.append((-30.03, 145.03, d, "0300", "N", "n", 2.0))
        # gas flare: static, flagged type 2 by archive
        m_rows.append((-20.0, 118.0, d, "0030", "Terra", "60", 30.0, 2))
    return pd.concat([normalise(_modis(m_rows)), normalise(_viirs(v_rows))], ignore_index=True)


def test_fuse_dedup_agreement_static():
    df = _scenario()
    r = fuse(df)
    assert r.detections_static_removed == 15                        # flare removed every day
    cd = r.cellday
    big = cd[np.isclose(r.grid.centre(cd.cell)[0], -34.95)]
    assert len(big) == 15 and (big.agree == "both").all()
    assert (big.n_pix == 1).all()                                    # 3 detections, 1 physical hotspot
    assert (big.n_platforms == 3).all()
    small = cd[np.isclose(r.grid.centre(cd.cell)[1], 145.05)]
    assert len(small) == 15 and (small.agree == "viirs_only").all()
    assert r.agreement["noaa20_only_cell_days"] == 0
    # noisy-OR fused confidence > best single confidence
    assert (big.q > 0.95).all()


def test_neighbour_tolerance():
    m = normalise(_modis([(-35.0, 149.099, "2019-12-01", "0030", "Aqua", "90", 50.0, 0)]))
    v = normalise(_viirs([(-35.0, 149.101, "2019-12-01", "0300", "N", "h", 60.0)]))
    r = fuse(pd.concat([m, v], ignore_index=True))
    assert set(r.cellday.agree) == {"both_nb"}


def test_flags_new_ignition_and_escalation():
    r = fuse(_scenario()); cell_flags(r)
    cd = r.cellday
    big = cd[np.isclose(r.grid.centre(cd.cell)[0], -34.95)].sort_values("day")
    f = big["flags"].values
    assert f[0] & NEW_IGNITION and not f[5] & NEW_IGNITION
    assert f[5] & ESCALATING


def test_alert_levels_surge_and_gates():
    active = np.r_[np.full(40, 10), np.full(10, 60)]
    frp = np.r_[np.full(40, 1500.0), np.full(10, 60000.0)]
    lvl, ratio = alert_levels(active, frp)
    assert lvl[:35].max() == 0 and lvl[-1] == 3 and ratio[-1] > 4
    # same surge but low intensity (savanna-type) -> gated to Normal
    lvl2, _ = alert_levels(np.r_[np.full(40, 10), np.full(10, 600)], np.r_[np.full(40, 300.0), np.full(10, 18000.0)])
    assert lvl2.max() == 0


def test_region_series_shapes():
    r = fuse(_scenario()); cell_flags(r)
    s = region_series(r, (140, -40, 155, -25))
    assert len(s) == len(r.days) and s["active"].sum() == len(r.cellday)
