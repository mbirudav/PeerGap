# PeerGap v2: what changed

The original pipeline and every number in the current deck are untouched.
`peergap.py`, `data/metros.csv`, `data/peers.csv`, `data/results.json` and `slides/` are byte-identical to the first commit.

## Run it

```bash
uv venv .venv && uv pip install -p .venv/bin/python -r requirements-v2.txt
.venv/bin/python build_v2.py --all     # writes outputs/v2/* and data/v2.json
python3 assemble.py                    # inlines data + libraries into index.html and forecast.html
open index.html                        # works offline, no server needed
```

Check the pages end to end (needs Chrome and Node 22+):

```bash
python3 -m http.server 8765 &
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new --remote-debugging-port=9333 --user-data-dir=/tmp/pg-chrome about:blank &
node tests/ui_check.mjs 9333 http://127.0.0.1:8765 "$PWD"    # 43 checks
```

## Pages

| Page | What it does |
|---|---|
| `index.html` | Map of the 100 metros. Click a city to draw thin gray lines to its 10 peers; darker lines are closer peers. The headline under the city name is the gap with its peers: big red when less affordable, big green when more. Toggle "Compared with peers" or "Renter squeeze", pick a what-if, and drag 2026 to 2036. |
| `forecast.html` | Pick a metro on the map, not a dropdown. The map recolours by each metro's scenario gap as the year moves. Gap and renter-burden bands to 2036, rank, and a year-by-year table, with and without the bank play. |
| `compare.html` | Two cities side by side. Click the map to set A, then B, or use a preset. Shows both headlines, whether they are peers and how close, shared peers, the three biggest differences, and five tabs: affordability, access, HOME drivers, how peers are matched, and a forecast overlay. Links like `compare.html#miami-vs-pittsburgh` open a pair directly. |

The classic view is deleted. The original page data now lives in `data/app_data.json`.
Editing the pages: change `src/*.html`, `src/shared.css` or `src/shared.js`, then run `python3 assemble.py`. Never edit the root HTML files by hand.

## Flags on `build_v2.py`

| Flag | Output in `outputs/v2/` | Status |
|---|---|---|
| `--peers "Miami"` | `miami_peers.csv`: the metro, its 10 peers with distance, matching factors and housing score, then the peer median. Any metro name works. | Real |
| `--clusters` | `clusters.csv`, `cluster_profiles.csv`, `cluster_names.csv`, `archetype_map.png`. K-means for k = 4 to 8 on the peer-matching factors only, seed 2026, best silhouette wins. | Real |
| `--returns` | `shared_equity_returns.csv`. Reproduces the five-row deck table to the dollar, plus a prices-fall-10% row. | Real arithmetic |
| `--divergence` | `divergence.csv`, `divergence_quadrant.png`, `funnel.csv`. | Illustrative |
| `--hmda-reasons` | `miami_denial_reasons.csv` + `.png`, `pnc_miami_impact.csv`, printed FLAGSHIP line. | Illustrative |
| `--scenarios` | `scenarios.csv`: every metro, three what-ifs, 2026 to 2036. | Scenario |
| `--qa` | `simulation_qa.md`: 12 questions the simulation can answer, each answered from the scenario data and tagged real, scenario or placeholder. Needs no website. | Mixed |
| `--all` | All of the above, plus `gap_metros.csv` with the HOME `route` column, `metro_coords.csv`, and `data/v2.json` for the pages. | Mixed |

## Real vs. illustrative

Every illustrative CSV column ends in `_assumption` or `_scenario`. Every illustrative element on the pages carries a **Phase 1** badge.

| Item | Status | What it rests on |
|---|---|---|
| Map coordinates | Real | Census 2023 CBSA gazetteer, 100 of 100 matched |
| Peers, peer table, gap, rank, Monte Carlo | Real | The existing pipeline outputs |
| Archetypes | Real | Best k = 4 (silhouette 0.199). New York formed a cluster of one and was folded into its nearest archetype, leaving 3. 81% of each metro's peers share its archetype. Names are blank for the team to fill in `cluster_names.csv`. |
| HOME router lanes | Real logic | Top driver vs. peers picks the lane. Condo rescue moves first only if collateral denials run 5+ points above peers. |
| Bank-play effect | Real arithmetic on an assumed play | Halves each metro's denial gap with its peers, then re-scores with the baseline standardization held fixed. Miami: 0.35 SD smaller, about 12% of its gap by 2031. |
| Denial reasons, FLAGSHIP line | **Placeholder** | Made-up shares, not data. Prints `FLAGSHIP: gap equity loan`. |
| PNC approvals | **Placeholder** | 1,240 PNC Miami denials is a stand-in. Formula is real: denials × fixable share × re-approval rate (0.25 / 0.50 / 0.75). |
| Divergence (gap then vs. now), trend labels | **Placeholder** | Built from growth, new-build and denial signals, not from a 2015 to 2019 rebuild. |
| 2026 to 2036 scenarios | Scenario | Today's value plus a stated drift. The band is the Monte Carlo spread, widening 8% a year. |

## Before anyone quotes a placeholder number

1. Pull HMDA 2025 loan-level records for CBSA 33100 and its 10 peers. Tabulate `denial_reason-1` and replace the placeholder shares.
2. Look up PNC Bank's LEI in GLEIF or the HMDA institution search, filter to it, and replace the 1,240.
3. Rebuild the six measures for ACS 2015 to 2019 plus HMDA 2019, on 2015 to 2019 peers, and replace the divergence stand-in.
4. Name the three archetypes in `cluster_names.csv` and rerun `--all`.

## Not in this repo

- `access_impact.py` and the "10% redirected" wording live in the deck, not in this code. Change that wording to "10% targeted at gap metros" in the deck.
- The old site at peergaplive.netlify.app is not in this Netlify account and was not touched.

## Deploy

Live at https://peergap.netlify.app (Netlify project `peergap`). Only the three pages are published. To redeploy after a change:

```bash
python3 assemble.py && mkdir -p dist && cp index.html forecast.html compare.html dist/
netlify deploy --prod --dir dist --site peergap
```
