#!/usr/bin/env python3
"""Inline shared CSS, the metro data and the US outline into the pages: src/*.html -> ./*.html"""
import json, re
from pathlib import Path
ROOT = Path(__file__).resolve().parent
legacy = (ROOT / "legacy.html").read_text()
m = re.search(r"const DATA *= *", legacy); i = m.end(); depth = 0
while True:
    ch = legacy[i]; depth += ch in "[{"; depth -= ch in "]}"
    if depth == 0: break
    i += 1
DATA = json.dumps(json.loads(legacy[m.end():i + 1]), separators=(",", ":"))
V2 = json.dumps(json.loads((ROOT / "data" / "v2.json").read_text()), separators=(",", ":"))
US = json.dumps(json.loads((ROOT / "data" / "us_states_albers.json").read_text()), separators=(",", ":"))
CSS = (ROOT / "src" / "shared.css").read_text()
D3 = (ROOT / "src" / "vendor" / "d3-7.9.0.min.js").read_text()          # inlined so the demo runs with no internet
TOPO = (ROOT / "src" / "vendor" / "topojson-3.0.2.min.js").read_text()
for src in sorted((ROOT / "src").glob("*.html")):
    html = src.read_text().replace("/*__SHARED_CSS__*/", CSS).replace("/*__DATA__*/", DATA).replace("/*__V2__*/", V2).replace("/*__US__*/", US).replace("/*__D3__*/", D3).replace("/*__TOPOJSON__*/", TOPO)
    (ROOT / src.name).write_text(html); print(f"assembled {src.name} ({len(html) // 1024} KB)")
