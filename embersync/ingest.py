"""
EmberSync — ingest layer.

Reads any NASA FIRMS active-fire CSV (MODIS C6/C6.1, VIIRS S-NPP / NOAA-20 / NOAA-21,
archive or NRT) and maps it onto ONE canonical schema. New sensors are added by
registering an adapter rule here; nothing downstream changes.

Canonical columns
-----------------
lat, lon            float   detection pixel centre (WGS84)
t_utc               datetime64  overpass time (UTC)
local_day           date    LOCAL SOLAR day (UTC + lon/15 h) – a night overpass at
                            01:00 local is not split from the evening fire it belongs to
sensor              str     MODIS | VIIRS
platform            str     Terra | Aqua | S-NPP | NOAA-20 | NOAA-21
platform_bit        int     bitmask id (1,2,4,8,16) used for fast set logic
conf_class          str     l | n | h  (harmonised – MODIS 0-100 mapped with FIRMS rule)
conf_p              float   0-1 probability-like confidence used for fusion
frp                 float   fire radiative power, MW
bt4                 float   mid-IR brightness temp (MODIS ch21/22, VIIRS I4), K
pixel_km2           float   actual footprint = scan * track (grows off-nadir)
daynight            str     D | N
src_type            int     FIRMS 'type' (0 veg fire, 1 volcano, 2 static land, 3 offshore);
                            -1 = not supplied (NRT products)
product             str     archive | nrt
"""
from __future__ import annotations

import pandas as pd
import numpy as np

PLATFORM_BITS = {"Terra": 1, "Aqua": 2, "S-NPP": 4, "NOAA-20": 8, "NOAA-21": 16}
SENSOR_OF = {"Terra": "MODIS", "Aqua": "MODIS", "S-NPP": "VIIRS", "NOAA-20": "VIIRS", "NOAA-21": "VIIRS"}

# FIRMS encodes the satellite differently per product – normalise every variant seen in the wild
_PLATFORM_ALIASES = {
    "terra": "Terra", "t": "Terra",
    "aqua": "Aqua", "a": "Aqua",
    "n": "S-NPP", "npp": "S-NPP", "suomi-npp": "S-NPP", "suomi npp": "S-NPP", "s-npp": "S-NPP", "snpp": "S-NPP",
    "1": "NOAA-20", "n20": "NOAA-20", "noaa-20": "NOAA-20", "noaa20": "NOAA-20", "j1": "NOAA-20",
    "2": "NOAA-21", "n21": "NOAA-21", "noaa-21": "NOAA-21", "noaa21": "NOAA-21", "j2": "NOAA-21",
}

# Probability-like value for each harmonised confidence class (used in noisy-OR fusion).
# VIIRS classes are categorical; MODIS keeps its continuous score.
CLASS_P = {"l": 0.30, "n": 0.70, "h": 0.95}


def modis_conf_to_class(c: pd.Series) -> pd.Series:
    """FIRMS rule: 0-29 low, 30-79 nominal, 80-100 high."""
    c = pd.to_numeric(c, errors="coerce").fillna(0)
    return pd.Series(np.select([c < 30, c < 80], ["l", "n"], "h"), index=c.index)


def _norm_platform(s: pd.Series, instrument: pd.Series) -> pd.Series:
    out = s.astype(str).str.strip().str.lower().map(_PLATFORM_ALIASES)
    # unknown code -> fall back on instrument (S-NPP is the long-running VIIRS default)
    fallback = np.where(instrument.astype(str).str.upper().str.contains("MODIS"), "Aqua", "S-NPP")
    return out.fillna(pd.Series(fallback, index=s.index))


def read_firms_csv(path: str) -> pd.DataFrame:
    """Read a single FIRMS CSV of any supported product into the canonical schema."""
    raw = pd.read_csv(path, dtype={"acq_time": str, "confidence": str, "version": str})
    return normalise(raw)


def normalise(raw: pd.DataFrame) -> pd.DataFrame:
    cols = set(raw.columns)
    instrument = raw.get("instrument", pd.Series("VIIRS" if "bright_ti4" in cols else "MODIS", index=raw.index))
    platform = _norm_platform(raw["satellite"], instrument)
    sensor = platform.map(SENSOR_OF)

    # time: acq_time is HHMM, sometimes loses leading zeros ("14" == 00:14)
    hhmm = raw["acq_time"].astype(str).str.zfill(4)
    t_utc = pd.to_datetime(raw["acq_date"].astype(str) + " " + hhmm.str[:2] + ":" + hhmm.str[2:], utc=True)
    lon = raw["longitude"].astype(float)
    local_day = (t_utc + pd.to_timedelta(lon / 15.0, unit="h")).dt.tz_localize(None).dt.normalize()

    # confidence harmonisation
    conf_raw = raw["confidence"].astype(str).str.strip().str.lower()
    is_modis = sensor.eq("MODIS")
    conf_class = conf_raw.str[0].where(~is_modis, modis_conf_to_class(raw["confidence"]))
    conf_class = conf_class.where(conf_class.isin(["l", "n", "h"]), "n")
    conf_p = conf_class.map(CLASS_P).astype(float)
    modis_num = pd.to_numeric(raw["confidence"], errors="coerce") / 100.0
    conf_p = conf_p.where(~is_modis, modis_num.clip(0.01, 0.99).fillna(0.5))

    bt4 = raw["bright_ti4"] if "bright_ti4" in cols else raw.get("brightness")
    product = np.where(raw.get("version", pd.Series("", index=raw.index)).astype(str).str.contains("NRT", case=False),
                       "nrt", "archive")

    df = pd.DataFrame({
        "lat": raw["latitude"].astype(float),
        "lon": lon,
        "t_utc": t_utc,
        "local_day": local_day,
        "sensor": sensor,
        "platform": platform,
        "platform_bit": platform.map(PLATFORM_BITS).astype("int16"),
        "conf_class": conf_class,
        "conf_p": conf_p.astype("float32"),
        "frp": pd.to_numeric(raw["frp"], errors="coerce").fillna(0).astype("float32"),
        "bt4": pd.to_numeric(bt4, errors="coerce").astype("float32"),
        "pixel_km2": (pd.to_numeric(raw["scan"], errors="coerce") * pd.to_numeric(raw["track"], errors="coerce")).astype("float32"),
        "daynight": raw.get("daynight", pd.Series("D", index=raw.index)).astype(str),
        "src_type": pd.to_numeric(raw["type"], errors="coerce").fillna(-1).astype("int8") if "type" in cols
                    else pd.Series(-1, index=raw.index, dtype="int8"),
        "product": product,
    })
    return df


def load_many(paths: list[str]) -> pd.DataFrame:
    frames = [read_firms_csv(p) for p in paths]
    df = pd.concat(frames, ignore_index=True)
    # exact duplicate rows appear when archive and NRT files overlap in time -> keep one (prefer archive)
    df = df.sort_values("product").drop_duplicates(["lat", "lon", "t_utc", "platform"], keep="first")
    return df.reset_index(drop=True)
