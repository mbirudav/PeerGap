# PeerGap pipeline — how to run it and what it does

## Run it (Mac terminal)

```bash
pip3 install pandas numpy matplotlib requests openpyxl
cd ~/Downloads/peergap            # or wherever you saved this folder
export CENSUS_API_KEY="paste_your_key_here"
python3 peergap.py
```

The first run downloads everything (a few minutes; the HMDA step makes about 110 small requests).
Every download is saved in `peergap_output/raw/`, so later runs are instant and give identical results.

After you choose a case-study metro, rerun with it:

```bash
python3 peergap.py --case "Denver"     # any unique part of the metro's name
```

`python3 peergap.py --demo` runs on fake data. It only tests that the code works. Never use demo output.

## What to open, in order

1. `validation_report.txt` — automated checks, then three metros to spot-check by hand.
2. `metros.csv` — every metro: metrics, score, gap, rank, twins, drivers, Monte Carlo results.
3. `peers.csv` — each metro's 10 twins and how close they are.
4. `charts/` — `gap_scatter.png`, `monte_carlo_top10.png`, and `case_drivers.png` (with `--case`).
5. `source_log.csv` — every Census variable used, with its official label. This is your data appendix.
6. `results.json` — feeds the interactive tool.

## The spot-check (do this before trusting anything)

The report lists three metros with five raw values each and the table ID (e.g., B25064).
For each one: go to data.census.gov, search the table ID plus the metro name, choose
"ACS 5-Year Estimates" for 2024, and confirm every number matches exactly. If any number
doesn't match, stop and tell me.

## What each step does, in plain English

**1. Download official data.** Census ACS 2020–2024 5-year estimates for every metro, the official
county-to-metro definitions (OMB, July 2023), and 2025 HMDA mortgage applications. No number is
typed by hand or produced by AI.

**2. Check every variable.** Before using a Census variable, the script downloads its official
label and confirms it's the right one (for example, that the code it uses for "renters paying 50%+"
really says "50.0 percent or more"). If a label doesn't match, the script stops.

**3. Pick the metros.** The 100 largest metropolitan statistical areas by population (Puerto Rico excluded).

**4. Find the twins.** Each metro becomes a list of numbers in four groups: size and growth,
income, job mix (13 industries), and infrastructure (broadband, transit use, households without
a car, commute time). Every number is put on the same scale, each group counts equally, and
the script measures the distance between every pair of metros. Each metro's 10 nearest are
its twins. Housing costs are deliberately left out, so they can't hide the gap.

**5. Score affordability and access.**
- Affordability: share of renters paying 30%+ of income, share paying 50%+, rent-to-income,
  price-to-income.
- Access: home-purchase mortgage denial rate (HMDA), homeownership rate.
- Each measure is standardized, averaged (affordability and access count equally), and the
  total is standardized again, so the gap is measured in standard deviations (SD).

**6. Measure the gap.** Gap = metro's score − median score of its 10 twins. +1.0 means one SD
worse than its twins.

**7. Compare drivers with twins (HOME).** For each metro, how far each driver is from its twins:
- H, housing supply: share of homes built since 2010; vacancy rate; share in 5+ unit buildings
- O, ownership access: mortgage denial rate
- M, maintaining stock: share of homes built before 1960
- E, expenses: median property tax relative to owner income

The biggest positive difference is the likely main driver. It's a lead to investigate, not proof of cause.

**8. Monte Carlo robustness test (1,000 runs).** Each run redraws every Census estimate within its
published margin of error, picks 5–15 twins, and randomly reweights the measures and the matching
groups. The output is how often each metro lands in the top 10 gaps. Case-study candidates are
metros with a positive gap that rank top 10 in at least 70% of runs.

## Limits to state honestly

- ACS covers 2020–2024, so it describes the recent past, not this month.
- The driver comparison shows where a metro differs from its twins; it suggests causes but doesn't prove them.
- The mortgage denial rate covers all home-purchase applicants, not only low- and moderate-income ones.
- Connecticut's county boundaries changed in 2022, so growth for Connecticut metros can't be computed
  on the same boundaries; it's treated as average in matching and flagged in the report.

## Repo layout

- `index.html` — the interactive demo (self-contained, data inlined; open it in a browser)
- `peergap.py` — the pipeline that produces everything below
- `data/` — `metros.csv`, `peers.csv`, `results.json`, `source_log.csv`, `case_study_table.csv`
- `docs/` — `validation_report.txt`, `policy_scan_miami.md`
- `slides/` — the three chart PNGs used in the deck
