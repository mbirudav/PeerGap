#!/usr/bin/env python3
"""Inline shared CSS, the metro data and the US outline into the pages: src/*.html -> ./*.html"""
import json, re
from pathlib import Path
ROOT = Path(__file__).resolve().parent
DATA = json.dumps(json.loads((ROOT / "data" / "app_data.json").read_text()), separators=(",", ":"))   # the original page data
V2 = json.dumps(json.loads((ROOT / "data" / "v2.json").read_text()), separators=(",", ":"))
US = json.dumps(json.loads((ROOT / "data" / "us_states_albers.json").read_text()), separators=(",", ":"))
CSS = (ROOT / "src" / "shared.css").read_text()
JS = (ROOT / "src" / "shared.js").read_text()
D3 = (ROOT / "src" / "vendor" / "d3-7.9.0.min.js").read_text()          # inlined so the demo runs with no internet
TOPO = (ROOT / "src" / "vendor" / "topojson-3.0.2.min.js").read_text()
for src in sorted((ROOT / "src").glob("*.html")):
    html = src.read_text().replace("/*__SHARED_CSS__*/", CSS).replace("/*__DATA__*/", DATA).replace("/*__V2__*/", V2).replace("/*__US__*/", US).replace("/*__SHARED_JS__*/", JS).replace("/*__D3__*/", D3).replace("/*__TOPOJSON__*/", TOPO)
    (ROOT / src.name).write_text(html); print(f"assembled {src.name} ({len(html) // 1024} KB)")
