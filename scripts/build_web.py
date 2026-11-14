"""Bundle the dashboard into ONE self-contained HTML file (works offline, on GitHub Pages, anywhere).

    python scripts/build_web.py            -> web/dist/index.html
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
web = ROOT / "web"
tpl = (web / "template.html").read_text()
payload = (ROOT / "data/out/web_payload.json").read_text().replace("</", "<\\/")
land = (web / "australia.geo.json").read_text().replace("</", "<\\/")
d3 = (web / "d3.min.js").read_text()
assert "</script" not in d3.lower()

base = tpl.replace("/*__PAYLOAD__*/", payload).replace("/*__LAND__*/", land)
out = web / "dist"
out.mkdir(exist_ok=True)
# hosted build: D3 from cdnjs (GitHub Pages / any static host)
cdn = base.replace("<script>/*__D3__*/</script>",
                   '<script src="https://cdnjs.cloudflare.com/ajax/libs/d3/7.9.0/d3.min.js"></script>')
(out / "index.html").write_text(cdn)
# offline build: everything inlined – for demo laptops without internet
(out / "index_offline.html").write_text(base.replace("/*__D3__*/", d3))
for f in ("index.html", "index_offline.html"):
    print("wrote", out / f, f"{(out / f).stat().st_size / 1e6:.2f} MB")
