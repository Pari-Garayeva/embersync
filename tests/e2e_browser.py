"""Headless browser smoke test: page renders, no JS errors, browser alert logic == Python alert logic."""
import json, sys
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
html = (ROOT / "web/dist/index_offline.html").as_uri()
shots = ROOT / "tests/shots"; shots.mkdir(exist_ok=True)
errors = []
with sync_playwright() as p:
    b = p.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-sandbox"])
    pg = b.new_page(viewport={"width": 1440, "height": 1000}, device_scale_factor=1)
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.on("console", lambda m: errors.append(m.text) if m.type == "error" and "Failed to load resource" not in m.text else None)  # fonts blocked in sandbox
    pg.goto(html); pg.wait_for_timeout(1500)
    pg.screenshot(path=str(shots / "01_full.png"), full_page=True)
    # region switch + parity check for NSW
    pg.select_option("#region", "New South Wales"); pg.wait_for_timeout(400)
    js = pg.evaluate("""() => { const s = window.__embersync.S.series; return {lvl: Array.from(s.lvl), active: Array.from(s.active), frp: Array.from(s.frp)} }""")
    pg.screenshot(path=str(shots / "02_nsw.png"), full_page=False)
    pg.click("#mSeason"); pg.click("#cAgr"); pg.wait_for_timeout(300)
    pg.locator("#mapwrap").screenshot(path=str(shots / "03_season_agreement.png"))
    pg.click("#mDay"); pg.click("#cInt")
    # draw a custom bbox via brush
    pg.click("#drawBtn"); box = pg.locator("#mapsvg").bounding_box()
    pg.mouse.move(box["x"] + box["width"] * .80, box["y"] + box["height"] * .62); pg.mouse.down()
    pg.mouse.move(box["x"] + box["width"] * .98, box["y"] + box["height"] * .80, steps=8); pg.mouse.up(); pg.wait_for_timeout(400)
    custom = pg.evaluate("() => ({bbox: window.__embersync.S.bbox, region: window.__embersync.S.region, cd: window.__embersync.S.series.active.reduce((a,b)=>a+b,0)})")
    pg.screenshot(path=str(shots / "04_custom.png"), full_page=False)
    csv = pg.evaluate("() => window.__embersync.csv()")
    # mobile
    m = b.new_page(viewport={"width": 390, "height": 844}); m.on("pageerror", lambda e: errors.append(str(e)))
    m.goto(html); m.wait_for_timeout(1200); m.screenshot(path=str(shots / "05_mobile.png"), full_page=False)
    b.close()

sys.path.insert(0, str(ROOT))
import glob
from embersync.ingest import load_many
from embersync.harmonize import fuse
from embersync.warning import cell_flags, region_series, REGIONS
r = fuse(load_many(sorted(glob.glob(str(ROOT / "data/raw/*.csv"))))); cell_flags(r)
py = region_series(r, REGIONS["New South Wales"])
lvl_match = (py["level"].values == js["lvl"]).mean()
act_match = (py["active"].values == js["active"]).mean()
print(json.dumps({"js_errors": errors, "alert_level_parity": float(lvl_match), "active_parity": float(act_match),
                  "custom_area": custom, "csv_lines": csv.count("\n"), "csv_head": csv.splitlines()[:2]}, indent=1))
assert not errors, errors
assert lvl_match == 1.0 and act_match == 1.0
print("E2E OK")
