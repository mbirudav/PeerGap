#!/usr/bin/env python3
"""
PeerGap pipeline - MARS Consulting (PNC x CMU 180DC Case Competition, Fall 2026)

What it does, in order:
  1. Downloads official data: Census ACS 2020-2024 5-year (via the Census API),
     the OMB/Census 2023 metro delineation file, and 2025 HMDA mortgage data
     (via the FFIEC/CFPB HMDA Data Browser API).
  2. Checks every Census variable against its official label before using it.
  3. Builds metrics for the 100 largest U.S. metro areas.
  4. Finds each metro's 10 closest "twins" on fundamentals (size, growth,
     income, job mix, infrastructure) - never on housing costs.
  5. Scores affordability + access, and measures each metro's gap vs. its twins.
  6. Compares each metro's housing drivers with its twins (mapped to HOME).
  7. Runs a 1,000-run Monte Carlo robustness test.
  8. Writes CSVs, charts, a validation report, a source log and results.json.

Run:
  export CENSUS_API_KEY="your_key_here"
  python3 peergap.py                 # full run (downloads are cached in peergap_output/raw)
  python3 peergap.py --case "Denver" # also makes the case-study table and chart
  python3 peergap.py --demo          # synthetic data, to test the code only (NOT real results)
"""

import argparse
import json
import math
import os
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests

# ----------------------------------------------------------------------------
# Settings (every choice here is a stated assumption; see Appendix B)
# ----------------------------------------------------------------------------
ACS_YEAR = 2024            # ACS 2020-2024 5-year estimates (released Jan 29, 2026)
ACS_PRIOR_YEAR = 2019      # ACS 2015-2019 5-year, used only for population growth
HMDA_YEAR = 2025           # 2025 HMDA data (published June 2026)
N_METROS = 100             # the 100 largest metropolitan statistical areas
K_PEERS = 10               # twins per metro
MC_RUNS = 1000             # Monte Carlo runs
MC_K_RANGE = (5, 15)       # number of twins varies between these in Monte Carlo
SEED = 2026                # fixed seed so results are reproducible

DELINEATION_URL = ("https://www2.census.gov/programs-surveys/metro-micro/geographies/"
                   "reference-files/2023/delineation-files/list1_2023.xlsx")
ACS_URL = "https://api.census.gov/data/{year}/acs/acs5"
HMDA_URL = "https://ffiec.cfpb.gov/v2/data-browser-api/view/aggregations"
CBSA_GEO = "metropolitan statistical area/micropolitan statistical area"

OUT = Path("peergap_output")
RAW = OUT / "raw"
CHARTS = OUT / "charts"

GREEN, ORANGE, RED, INK, MUTED = "#08804F", "#EF6A00", "#C41230", "#1D2125", "#5E6670"

LOG_LINES = []
SOURCE_LOG = []


def log(msg):
    print(msg)
    LOG_LINES.append(msg)


# ----------------------------------------------------------------------------
# Download helpers (every response is cached so reruns are identical)
# ----------------------------------------------------------------------------
def get_json(url, params, cache_name, retries=4):
    path = RAW / f"{cache_name}.json"
    if path.exists():
        return json.loads(path.read_text())
    last = ""
    for attempt in range(retries):
        try:
            r = requests.get(url, params=params, timeout=90)
            if r.status_code == 200:
                try:
                    data = r.json()
                except ValueError:
                    last = ("Response was not JSON. If this is a Census call, your API key may be "
                            "wrong or not activated yet (click the link in the Census email). "
                            f"First 200 chars: {r.text[:200]}")
                    break
                path.write_text(json.dumps(data))
                return data
            last = f"HTTP {r.status_code}: {r.text[:300]}"
            if r.status_code == 400:
                break  # bad request: retrying will not help
        except requests.RequestException as e:
            last = str(e)
        time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"Download failed [{cache_name}]: {last}")


def census_key():
    key = os.environ.get("CENSUS_API_KEY", "").strip()
    if not key:
        sys.exit("ERROR: set your key first:  export CENSUS_API_KEY=\"your_key_here\"")
    return key


# ----------------------------------------------------------------------------
# Census variable lookup: select variables by their OFFICIAL label, then log them
# ----------------------------------------------------------------------------
def group_meta(year, table):
    data = get_json(f"{ACS_URL.format(year=year)}/groups/{table}.json", {}, f"meta_{year}_{table}")
    return {code: v["label"] for code, v in data["variables"].items()
            if code.endswith("E") and code.startswith(table)}


def find_var(meta, table, contains, depth=None):
    """Exactly one variable whose official label contains every string.
    Prefers the given label depth; otherwise takes the shallowest (the 'Total' level)."""
    cands = {c: lab for c, lab in meta.items() if all(s.lower() in lab.lower() for s in contains)}
    hits = [c for c, lab in cands.items() if depth is not None and lab.count("!!") == depth]
    if not hits and cands:
        min_d = min(lab.count("!!") for lab in cands.values())
        hits = [c for c, lab in cands.items() if lab.count("!!") == min_d]
    if len(hits) != 1:
        raise ValueError(f"{table}: expected 1 match for {contains}, found {hits}. "
                         "Labels may have changed; stop and check.")
    return hits[0]


def build_variable_plan(year):
    """Returns {column_name: [list of ACS variable codes summed into it]}."""
    plan = {}
    m = {t: group_meta(year, t) for t in
         ["B01003", "B19013", "B25119", "B25064", "B25077", "B25070", "B25003", "B25002",
          "B28002", "B08301", "B08201", "B08013", "B08303", "B25103", "C24050", "B25034", "B25024"]}

    def one(col, table, contains, depth):
        plan[col] = [find_var(m[table], table, contains, depth)]

    one("pop", "B01003", ["total"], 1)
    one("hh_inc", "B19013", ["median household income"], 1)
    one("own_inc", "B25119", ["owner occupied"], 3)
    one("rent_inc", "B25119", ["renter occupied"], 3)
    one("rent", "B25064", ["median gross rent"], 1)
    one("value", "B25077", ["median value"], 1)
    one("b70_total", "B25070", ["total"], 1)
    one("b70_nc", "B25070", ["not computed"], 2)
    one("b70_30", "B25070", ["30.0 to 34.9"], 2)
    one("b70_35", "B25070", ["35.0 to 39.9"], 2)
    one("b70_40", "B25070", ["40.0 to 49.9"], 2)
    one("b70_50", "B25070", ["50.0 percent or more"], 2)
    one("ten_total", "B25003", ["total"], 1)
    one("ten_own", "B25003", ["owner occupied"], 2)
    one("units_total", "B25002", ["total"], 1)
    one("units_vacant", "B25002", ["vacant"], 2)
    one("bb_total", "B28002", ["total"], 1)
    one("bb_yes", "B28002", ["broadband of any type"], 3)
    one("tr_total", "B08301", ["total"], 1)
    one("tr_transit", "B08301", ["public transportation"], 2)
    one("veh_total", "B08201", ["total"], 1)
    one("veh_none", "B08201", ["no vehicle available"], 2)
    one("tt_agg", "B08013", ["aggregate travel time"], 1)
    one("tt_workers", "B08303", ["total"], 1)
    one("tax_med", "B25103", ["median real estate taxes"], 1)
    one("ind_total", "C24050", ["total"], 1)

    # C24050 lists 13 industry totals AND 5 occupation-group totals at the same level.
    # Keep only the industries (their labels never contain "occupations").
    industries = sorted(c for c, lab in m["C24050"].items()
                        if lab.count("!!") == 2 and "occupations" not in lab.lower())
    if len(industries) != 13:
        found = "\n  ".join(f"{c}: {m['C24050'][c]}" for c in industries)
        raise ValueError(f"C24050: expected 13 industries, found {len(industries)}:\n  {found}")
    log("Industries used (C24050):")
    for c in industries:
        log(f"  {c}: {m['C24050'][c].split('!!')[-1]}")
    for c in industries:
        plan[f"ind_{c}"] = [c]

    one("yb_total", "B25034", ["total"], 1)
    one("yb_2020p", "B25034", ["2020 or later"], 2)
    one("yb_2010s", "B25034", ["2010 to 2019"], 2)
    plan["yb_pre1960"] = [find_var(m["B25034"], "B25034", [s], 2)
                          for s in ["1950 to 1959", "1940 to 1949", "1939 or earlier"]]
    one("us_total", "B25024", ["total"], 1)
    plan["us_5plus"] = [find_var(m["B25024"], "B25024", [s], 2)
                        for s in ["5 to 9", "10 to 19", "20 to 49", "50 or more"]]

    for col, codes in plan.items():
        for c in codes:
            table = c.split("_")[0]
            SOURCE_LOG.append({"column": col, "dataset": f"ACS {year - 4}-{year} 5-year",
                               "table": table, "variable": c, "official_label": m[table][c]})
    return plan


def clean_acs(series, is_moe=False):
    s = pd.to_numeric(series, errors="coerce")
    if is_moe:
        s = s.where(s != -555555555, 0.0)   # "-555555555" = controlled estimate, no sampling error
    return s.where(s >= 0)                  # other negative codes = not available


def fetch_acs(year, geo, codes, key, cache_prefix):
    """Fetch estimates (E) and margins of error (M) for codes, in batches of 20 variables."""
    frames = []
    codes = sorted(set(codes))
    for i in range(0, len(codes), 20):
        batch = codes[i:i + 20]
        get = ["NAME"] + batch + [c[:-1] + "M" for c in batch]
        params = {"get": ",".join(get), "for": f"{geo}:*", "key": key}
        data = get_json(ACS_URL.format(year=year), params, f"{cache_prefix}_{i}")
        df = pd.DataFrame(data[1:], columns=data[0])
        frames.append(df.set_index(df.columns[-1]) if geo == CBSA_GEO else
                      df.assign(fips=df["state"] + df["county"]).set_index("fips"))
    out = frames[0]
    for f in frames[1:]:
        out = out.join(f.drop(columns=[c for c in f.columns if c in out.columns]), how="outer")
    return out


# ----------------------------------------------------------------------------
# Delineation file (official county -> metro definitions, July 2023)
# ----------------------------------------------------------------------------
def load_delineation():
    path = RAW / "list1_2023.xlsx"
    if not path.exists():
        r = requests.get(DELINEATION_URL, timeout=90)
        r.raise_for_status()
        path.write_bytes(r.content)
    raw = pd.read_excel(path, header=None, dtype=str)
    hdr = raw.index[raw.apply(lambda row: row.astype(str).str.strip().eq("CBSA Code").any(), axis=1)][0]
    df = pd.read_excel(path, header=hdr, dtype=str)
    need = ["CBSA Code", "Metropolitan Division Code", "CBSA Title",
            "Metropolitan/Micropolitan Statistical Area", "FIPS State Code", "FIPS County Code"]
    missing = [c for c in need if c not in df.columns]
    if missing:
        raise ValueError(f"Delineation file columns changed. Missing {missing}. Found {list(df.columns)}")
    df = df[df["CBSA Code"].astype(str).str.fullmatch(r"\d{5}")].copy()
    df["fips"] = df["FIPS State Code"].str.zfill(2) + df["FIPS County Code"].str.zfill(3)
    SOURCE_LOG.append({"column": "metro definitions", "dataset": "OMB/Census delineation (July 2023)",
                       "table": "list1_2023.xlsx", "variable": "", "official_label": DELINEATION_URL})
    return df


# ----------------------------------------------------------------------------
# HMDA (2025): home-purchase denial rate per metro
# ----------------------------------------------------------------------------
# The HMDA aggregation API allows at most TWO filters per request (besides year and geography),
# so we use exactly two: action taken and loan purpose = home purchase.
HMDA_FILTERS = {"loan_purposes": "1"}


def hmda_counts(msamd):
    params = {"years": HMDA_YEAR, "msamds": msamd, "actions_taken": "1,2,3", **HMDA_FILTERS}
    data = get_json(HMDA_URL, params, f"hmda_{HMDA_YEAR}_{msamd}_purchase")
    if "aggregations" not in data:
        raise RuntimeError(f"Unexpected HMDA response for {msamd}: {str(data)[:300]}")
    counts = {"1": 0, "2": 0, "3": 0}
    for a in data["aggregations"]:
        k = str(a.get("actions_taken"))
        counts[k] = counts.get(k, 0) + int(a["count"])
    return counts


def fetch_hmda(metros, delin):
    rows = {}
    for n, cbsa in enumerate(metros.index, 1):
        divs = delin.loc[delin["CBSA Code"] == cbsa, "Metropolitan Division Code"].dropna().unique()
        codes = list(divs) if len(divs) else [cbsa]
        tot = {"1": 0, "2": 0, "3": 0}
        for code in codes:
            c = hmda_counts(code)
            for k in tot:
                tot[k] += c[k]
            time.sleep(0.25)
        rows[cbsa] = {"hmda_c1": tot["1"], "hmda_c2": tot["2"], "hmda_c3": tot["3"],
                      "hmda_codes": "+".join(codes)}
        if n % 10 == 0:
            print(f"  HMDA: {n}/{len(metros)} metros downloaded")
    SOURCE_LOG.append({"column": "denial", "dataset": f"HMDA {HMDA_YEAR} (FFIEC/CFPB Data Browser API)",
                       "table": "aggregations", "variable": "actions_taken 1,2,3; loan_purposes 1",
                       "official_label": "All home-purchase applications; denial rate = denied / "
                                         "(originated + approved not accepted + denied). Metros with "
                                         "metropolitan divisions are summed across their divisions."})
    return pd.DataFrame.from_dict(rows, orient="index")


# ----------------------------------------------------------------------------
# Assemble the base table for the 100 largest metros
# ----------------------------------------------------------------------------
def build_base(key):
    RAW.mkdir(parents=True, exist_ok=True)
    delin = load_delineation()
    metro_codes = set(delin.loc[(delin["Metropolitan/Micropolitan Statistical Area"] ==
                                 "Metropolitan Statistical Area") & (delin["FIPS State Code"] != "72"),
                                "CBSA Code"])
    plan = build_variable_plan(ACS_YEAR)
    all_codes = [c for codes in plan.values() for c in codes]
    acs = fetch_acs(ACS_YEAR, CBSA_GEO, all_codes, key, f"acs_{ACS_YEAR}_cbsa")

    base = pd.DataFrame(index=acs.index)
    base["name"] = acs["NAME"]
    for col, codes in plan.items():
        est = sum(clean_acs(acs[c]) for c in codes)
        moe = np.sqrt(sum(clean_acs(acs[c[:-1] + "M"], True) ** 2 for c in codes))
        base[col], base[col + "_moe"] = est, moe

    base = base[base.index.isin(metro_codes)].sort_values("pop", ascending=False).head(N_METROS)
    log(f"Selected {len(base)} metros: largest = {base['name'].iloc[0]}, "
        f"smallest = {base['name'].iloc[-1]} (pop {base['pop'].iloc[-1]:,.0f})")

    # Population growth from COUNTY data summed to the 2023 metro definitions,
    # so both years use the same boundaries.
    prior_meta = group_meta(ACS_PRIOR_YEAR, "B01003")
    prior_code = find_var(prior_meta, "B01003", ["total"], 1)
    cty_now = fetch_acs(ACS_YEAR, "county", [plan["pop"][0]], key, f"acs_{ACS_YEAR}_county_pop")
    cty_old = fetch_acs(ACS_PRIOR_YEAR, "county", [prior_code], key, f"acs_{ACS_PRIOR_YEAR}_county_pop")
    SOURCE_LOG.append({"column": "pop_prior", "dataset": f"ACS {ACS_PRIOR_YEAR - 4}-{ACS_PRIOR_YEAR} 5-year",
                       "table": "B01003", "variable": prior_code, "official_label": prior_meta[prior_code]})
    now_s = clean_acs(cty_now[plan["pop"][0]])
    old_s = clean_acs(cty_old[prior_code])
    for cbsa in base.index:
        fips = delin.loc[delin["CBSA Code"] == cbsa, "fips"]
        missing_old = [f for f in fips if f not in old_s.index or pd.isna(old_s.get(f))]
        base.loc[cbsa, "pop_cty_now"] = now_s.reindex(fips).sum()
        base.loc[cbsa, "pop_cty_old"] = np.nan if missing_old else old_s.reindex(fips).sum()
        base.loc[cbsa, "growth_note"] = ("county boundaries changed (e.g., Connecticut planning regions); "
                                         "growth not computed" if missing_old else "")

    hmda = fetch_hmda(base, delin)
    base = base.join(hmda)
    return base, delin


# ----------------------------------------------------------------------------
# Metrics, features, drivers
# ----------------------------------------------------------------------------
def compute_metrics(b):
    m = pd.DataFrame(index=b.index)
    denom = b["b70_total"] - b["b70_nc"]
    m["burden"] = (b["b70_30"] + b["b70_35"] + b["b70_40"] + b["b70_50"]) / denom
    m["severe"] = b["b70_50"] / denom
    m["rent_to_income"] = 12 * b["rent"] / b["rent_inc"]
    m["price_to_income"] = b["value"] / b["hh_inc"]
    apps = b["hmda_c1"] + b["hmda_c2"] + b["hmda_c3"]
    m["denial"] = b["hmda_c3"] / apps
    m["homeownership"] = b["ten_own"] / b["ten_total"]
    # matching features
    m["log_pop"] = np.log(b["pop"])
    m["pop_growth"] = b["pop_cty_now"] / b["pop_cty_old"] - 1
    m["med_hh_income"] = b["hh_inc"]
    for c in [c for c in b.columns if c.startswith("ind_C24050") and not c.endswith("_moe")]:
        m["share_" + c[4:]] = b[c] / b["ind_total"]
    m["broadband"] = b["bb_yes"] / b["bb_total"]
    m["transit_share"] = b["tr_transit"] / b["tr_total"]
    m["no_vehicle"] = b["veh_none"] / b["veh_total"]
    m["commute_min"] = b["tt_agg"] / b["tt_workers"]
    # drivers
    m["recent_build"] = (b["yb_2020p"] + b["yb_2010s"]) / b["yb_total"]
    m["vacancy"] = b["units_vacant"] / b["units_total"]
    m["multifamily"] = b["us_5plus"] / b["us_total"]
    m["pre1960"] = b["yb_pre1960"] / b["yb_total"]
    m["tax_burden"] = b["tax_med"] / b["own_inc"]
    return m


AFF = ["burden", "severe", "rent_to_income", "price_to_income"]   # higher = worse
ACC = ["denial", "homeownership"]                                  # denial higher = worse; ownership lower = worse
ACC_SIGN = {"denial": 1, "homeownership": -1}

# driver, sign (+1 means a HIGHER value hurts affordability), HOME letter, plain label
DRIVERS = [
    ("recent_build", -1, "H", "Homes built since 2010 (share of stock)"),
    ("vacancy", -1, "H", "Vacancy rate"),
    ("multifamily", -1, "H", "Apartments in 5+ unit buildings (share)"),
    ("denial", 1, "O", "Home-purchase mortgage denial rate"),
    ("pre1960", 1, "M", "Homes built before 1960 (share)"),
    ("tax_burden", 1, "E", "Median property tax / owner income"),
]
HOME_NAMES = {"H": "Housing supply", "O": "Ownership access",
              "M": "Maintaining existing stock", "E": "Expenses of ownership"}


def z(s):
    sd = s.std(ddof=0)
    return (s - s.mean()) / sd if sd > 0 else s * 0


def feature_dims(m):
    return {"size_growth": ["log_pop", "pop_growth"],
            "income": ["med_hh_income"],
            "jobs": [c for c in m.columns if c.startswith("share_")],
            "infrastructure": ["broadband", "transit_share", "no_vehicle", "commute_min"]}


def distance_matrix(m, dim_weights=None):
    """Weighted Euclidean distance on z-scored fundamentals; each dimension gets equal total weight."""
    dims = feature_dims(m)
    dim_weights = dim_weights or {d: 1.0 for d in dims}
    blocks = []
    for d, cols in dims.items():
        zb = m[cols].apply(z).fillna(0.0)          # missing -> metro average (logged separately)
        blocks.append(zb.values * math.sqrt(dim_weights[d] / len(cols)))
    X = np.hstack(blocks)
    D = np.sqrt(((X[:, None, :] - X[None, :, :]) ** 2).sum(axis=2))
    np.fill_diagonal(D, np.inf)
    return D


def score(m, aff_w=None, acc_w=None, alpha=0.5):
    aff_w = aff_w if aff_w is not None else np.ones(len(AFF)) / len(AFF)
    acc_w = acc_w if acc_w is not None else np.ones(len(ACC)) / len(ACC)
    aff = sum(w * z(m[c]) for w, c in zip(aff_w, AFF))
    acc = sum(w * ACC_SIGN[c] * z(m[c]) for w, c in zip(acc_w, ACC))
    return z(alpha * aff + (1 - alpha) * acc), aff, acc   # standardized, so the gap is in SD units


def peers_and_gap(m, k=K_PEERS, dim_weights=None, **score_kw):
    D = distance_matrix(m, dim_weights)
    idx = np.argsort(D, axis=1)[:, :k]
    s, aff, acc = score(m, **score_kw)
    sv = s.values
    peer_median = np.array([np.median(sv[row]) for row in idx])
    gap = pd.Series(sv - peer_median, index=m.index)
    return s, aff, acc, pd.Series(peer_median, index=m.index), gap, idx, D


def driver_table(m, idx):
    out = pd.DataFrame(index=m.index)
    for col, sign, letter, _ in DRIVERS:
        zc = z(m[col]).values
        dev = np.array([zc[i] - np.median(zc[row]) for i, row in enumerate(idx)])
        out["drv_" + col] = sign * dev        # positive = worse than twins in a way that hurts affordability
    return out


# ----------------------------------------------------------------------------
# Monte Carlo robustness test
# ----------------------------------------------------------------------------
def monte_carlo(base, runs=MC_RUNS):
    rng = np.random.default_rng(SEED)
    est_cols = [c for c in base.columns if c + "_moe" in base.columns]
    n = len(base)
    ranks = np.zeros((runs, n))
    gaps = np.zeros((runs, n))
    for r in range(runs):
        b = base.copy()
        for c in est_cols:                     # redraw every estimate within its margin of error
            se = base[c + "_moe"].fillna(0) / 1.645
            b[c] = np.maximum(base[c] + rng.normal(0, 1, n) * se, 0)
        m = compute_metrics(b)
        k = int(rng.integers(MC_K_RANGE[0], MC_K_RANGE[1] + 1))
        dims = feature_dims(m)
        dw = dict(zip(dims, rng.uniform(0.5, 1.5, len(dims))))
        _, _, _, _, gap, _, _ = peers_and_gap(
            m, k=k, dim_weights=dw, aff_w=rng.dirichlet(np.ones(len(AFF))),
            acc_w=rng.dirichlet(np.ones(len(ACC))), alpha=rng.uniform(0.3, 0.7))
        gaps[r] = gap.values
        ranks[r] = gap.rank(ascending=False, method="min").values
    res = pd.DataFrame(index=base.index)
    res["mc_top3_share"] = (ranks <= 3).mean(axis=0)
    res["mc_top10_share"] = (ranks <= 10).mean(axis=0)
    res["mc_median_rank"] = np.median(ranks, axis=0)
    res["mc_gap_p10"] = np.percentile(gaps, 10, axis=0)
    res["mc_gap_p50"] = np.percentile(gaps, 50, axis=0)
    res["mc_gap_p90"] = np.percentile(gaps, 90, axis=0)
    return res


# ----------------------------------------------------------------------------
# Validation
# ----------------------------------------------------------------------------
def validate(base, m):
    issues = []

    def check(cond, msg):
        if not cond:
            issues.append(msg)

    for c in ["burden", "severe", "denial", "homeownership", "vacancy", "recent_build", "pre1960",
              "multifamily", "broadband", "transit_share", "no_vehicle"]:
        bad = m[(m[c] < 0) | (m[c] > 1)].index.tolist()
        check(not bad, f"{c} outside 0-100% for: {bad}")
    check((m["severe"] <= m["burden"] + 1e-9).all(), "severe burden exceeds burden somewhere")
    ind_cols = [c for c in base.columns if c.startswith("ind_C24050") and not c.endswith("_moe")]
    ind_gap = (base[ind_cols].sum(axis=1) / base["ind_total"] - 1).abs()
    check((ind_gap < 0.005).all(), f"industry counts don't sum to total for {ind_gap[ind_gap >= 0.005].index.tolist()}")
    pop_gap = (base["pop_cty_now"] / base["pop"] - 1).abs()
    check((pop_gap < 0.005).all(),
          f"county sum != metro population for {base.index[pop_gap >= 0.005].tolist()}")
    apps = base["hmda_c1"] + base["hmda_c2"] + base["hmda_c3"]
    check((apps >= 1000).all(), f"fewer than 1,000 HMDA applications: {base.index[apps < 1000].tolist()}")
    core = AFF + ACC
    nan_core = m[core].isna().any(axis=1)
    check(not nan_core.any(), f"missing core metrics for: {m.index[nan_core].tolist()}")
    if (base["value"] >= 2_000_000).any():
        issues.append(f"median home value top-coded (>= $2M) for: {base.index[base['value'] >= 2_000_000].tolist()}")
    growth_missing = base.index[base["pop_cty_old"].isna()].tolist()
    if growth_missing:
        issues.append(f"population growth not computed (boundary change) for {growth_missing}; "
                      "treated as average in matching")
    return issues


# ----------------------------------------------------------------------------
# Outputs
# ----------------------------------------------------------------------------
SHORT_OVERRIDES = {"Winston": "Winston-Salem"}


def short_name(full):
    s = full.split(",")[0].split("--")[0].split("-")[0].split("/")[0].strip()
    return SHORT_OVERRIDES.get(s, s)


def make_charts(res, mc, case_id=None):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    CHARTS.mkdir(parents=True, exist_ok=True)
    plt.rcParams.update({"font.size": 12, "axes.edgecolor": MUTED, "axes.labelcolor": INK})

    # 1. Gap scatter
    fig, ax = plt.subplots(figsize=(11, 7.5), dpi=200)
    x, y = res["peer_median_score"], res["score"]
    colors = np.where(res["gap"] > 0.5, RED, np.where(res["gap"] < -0.5, GREEN, "#B9B2A6"))
    ax.scatter(x, y, s=46, c=colors, edgecolor="white", linewidth=0.6, zorder=3)
    lo, hi = min(x.min(), y.min()) - 0.3, max(x.max(), y.max()) + 0.3
    ax.plot([lo, hi], [lo, hi], color=MUTED, lw=1, ls="--", zorder=2)
    for i in list(res["gap"].nlargest(8).index) + list(res["gap"].nsmallest(3).index):
        ax.annotate(res.loc[i, "short"], (x[i], y[i]), xytext=(6, 4), textcoords="offset points",
                    fontsize=10, color=INK)
    if case_id is not None:
        ax.scatter([x[case_id]], [y[case_id]], s=140, facecolor="none", edgecolor=ORANGE, lw=2.5, zorder=4)
    ax.set_xlabel("What its twins look like (median twin score)")
    ax.set_ylabel("Actual affordability + access score (higher = worse)")
    ax.text(lo + 0.1, hi - 0.25, "Above the line = worse than its twins", color=RED, fontsize=11)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(CHARTS / "gap_scatter.png")
    plt.close(fig)

    # 2. Monte Carlo robustness
    top = mc.join(res[["short"]]).sort_values("mc_top10_share", ascending=False).head(15)[::-1]
    fig, ax = plt.subplots(figsize=(10, 7), dpi=200)
    ax.barh(top["short"], top["mc_top10_share"] * 100, color=GREEN)
    for i, v in enumerate(top["mc_top10_share"] * 100):
        ax.text(v + 1, i, f"{v:.0f}%", va="center", fontsize=10)
    ax.set_xlabel(f"Share of {MC_RUNS:,} simulations in which the metro ranks in the top 10 gaps")
    ax.set_xlim(0, 110)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(CHARTS / "monte_carlo_top10.png")
    plt.close(fig)

    # 3. Case-study drivers
    if case_id is not None:
        labels = [lab for _, _, _, lab in DRIVERS]
        vals = [res.loc[case_id, "drv_" + c] for c, _, _, _ in DRIVERS]
        fig, ax = plt.subplots(figsize=(10, 5.5), dpi=200)
        ax.barh(labels[::-1], vals[::-1], color=[RED if v > 0 else GREEN for v in vals[::-1]])
        ax.axvline(0, color=MUTED, lw=1)
        ax.set_xlabel("Difference from twin median (SD). Right = worse than twins")
        ax.spines[["top", "right"]].set_visible(False)
        fig.tight_layout()
        fig.savefig(CHARTS / "case_drivers.png")
        plt.close(fig)


def write_outputs(base, m, res, mc, idx, D, issues, case_id):
    OUT.mkdir(parents=True, exist_ok=True)
    names = res["short"].values
    peer_rows = []
    for i, cbsa in enumerate(res.index):
        for r, j in enumerate(idx[i]):
            peer_rows.append({"metro": res.loc[cbsa, "name"], "rank": r + 1,
                              "twin": res["name"].iloc[j], "distance": round(float(D[i, j]), 3)})
    pd.DataFrame(peer_rows).to_csv(OUT / "peers.csv", index=False)

    full = res.join(mc)
    full.sort_values("gap", ascending=False).to_csv(OUT / "metros.csv")
    pd.DataFrame(SOURCE_LOG).to_csv(OUT / "source_log.csv", index=False)

    # results.json for the interactive tool
    metros = []
    for i, cbsa in enumerate(res.index):
        r = full.loc[cbsa]
        drivers = [{"key": c, "label": lab, "home": letter, "dev": round(float(r["drv_" + c]), 3)}
                   for c, _, letter, lab in DRIVERS]
        metros.append({
            "cbsa": cbsa, "name": r["name"], "short": r["short"],
            "gap": round(float(r["gap"]), 3), "gap_rank": int(r["gap_rank"]),
            "score": round(float(r["score"]), 3),
            "twins": [names[j] for j in idx[i]],
            "metrics": {c: (None if pd.isna(r[c]) else round(float(r[c]), 4)) for c in AFF + ACC},
            "drivers": drivers, "top_driver_home": r["top_driver_home"],
            "mc_top10_share": round(float(r["mc_top10_share"]), 3),
            "mc_gap_p10": round(float(r["mc_gap_p10"]), 3), "mc_gap_p90": round(float(r["mc_gap_p90"]), 3)})
    (OUT / "results.json").write_text(json.dumps({"acs": f"ACS {ACS_YEAR - 4}-{ACS_YEAR} 5-year",
                                                  "hmda": HMDA_YEAR, "k": K_PEERS, "runs": MC_RUNS,
                                                  "metros": metros}, indent=1))

    # validation report with spot-check instructions
    lines = ["PEERGAP VALIDATION REPORT", "=" * 60, ""]
    lines += ["AUTOMATED CHECKS:"] + ([f"  ! {x}" for x in issues] if issues else ["  All checks passed."])
    lines += ["", "SPOT-CHECK THESE AGAINST data.census.gov (search the table ID + metro name,",
              f"choose 'ACS 5-Year Estimates', year {ACS_YEAR}). Every value must match exactly:", ""]
    spot = [base.index[0], base.index[len(base) // 2]] + ([case_id] if case_id else [base.index[-1]])
    for cb in spot:
        b = base.loc[cb]
        lines += [f"  {b['name']} (CBSA {cb})",
                  f"    B01003 total population ........ {b['pop']:,.0f}",
                  f"    B19013 median household income . ${b['hh_inc']:,.0f}",
                  f"    B25064 median gross rent ....... ${b['rent']:,.0f}",
                  f"    B25077 median home value ....... ${b['value']:,.0f}",
                  f"    B25070 rent >=50% of income .... {b['b70_50']:,.0f} households", ""]
    lines += ["", "RUN LOG:"] + LOG_LINES
    (OUT / "validation_report.txt").write_text("\n".join(lines))


def summarize(res, mc, case_id):
    full = res.join(mc)
    log("\nTOP 10 GAPS VS. TWINS (SD above twin median; higher = worse):")
    for cbsa, r in full.sort_values("gap", ascending=False).head(10).iterrows():
        log(f"  {int(r['gap_rank']):>3}. {r['name'][:48]:<48} gap {r['gap']:+.2f}  "
            f"top-10 in {r['mc_top10_share'] * 100:3.0f}% of runs  main driver: "
            f"{HOME_NAMES.get(r['top_driver_home'], '-')}")
    cand = full[(full["gap"] > 0) & (full["mc_top10_share"] >= 0.7)].sort_values("gap", ascending=False)
    log("\nCASE-STUDY CANDIDATES (positive gap, top 10 in >=70% of simulations):")
    for _, r in cand.head(5).iterrows():
        log(f"  {r['name']}  (gap {r['gap']:+.2f}, robust {r['mc_top10_share'] * 100:.0f}%, "
            f"driver {HOME_NAMES.get(r['top_driver_home'], '-')})")
    if case_id is not None:
        r = full.loc[case_id]
        log(f"\nCASE STUDY: {r['name']}  gap {r['gap']:+.2f} SD (rank {int(r['gap_rank'])}), "
            f"P10-P90 across simulations {r['mc_gap_p10']:+.2f} to {r['mc_gap_p90']:+.2f}")


# ----------------------------------------------------------------------------
# Demo data (synthetic) - only to test the code runs
# ----------------------------------------------------------------------------
def demo_base(n=N_METROS):
    rng = np.random.default_rng(1)
    b = pd.DataFrame(index=[f"{10000 + i}" for i in range(n)])
    b["name"] = [f"Demo City {i}-Town, ZZ Metro Area" for i in range(n)]
    pop = np.sort(rng.lognormal(14, 0.8, n))[::-1] + 5e5
    b["pop"], b["pop_cty_now"] = pop, pop
    b["pop_cty_old"] = pop / rng.uniform(1.0, 1.12, n)
    b["hh_inc"] = rng.normal(80000, 15000, n)
    b["own_inc"] = b["hh_inc"] * 1.3
    b["rent_inc"] = b["hh_inc"] * 0.6
    b["rent"] = b["rent_inc"] / 12 * rng.uniform(0.22, 0.4, n)
    b["value"] = b["hh_inc"] * rng.uniform(3, 8, n)
    b["b70_total"] = pop * 0.15
    b["b70_nc"] = b["b70_total"] * 0.05
    sh = rng.dirichlet([6, 1, 1, 1.2, 2], n)
    for c, s in zip(["_rest", "b70_30", "b70_35", "b70_40", "b70_50"], sh.T):
        if c != "_rest":
            b[c] = (b["b70_total"] - b["b70_nc"]) * s
    b["ten_total"] = pop * 0.38
    b["ten_own"] = b["ten_total"] * rng.uniform(0.5, 0.72, n)
    b["units_total"] = pop * 0.42
    b["units_vacant"] = b["units_total"] * rng.uniform(0.04, 0.12, n)
    b["bb_total"] = b["ten_total"]
    b["bb_yes"] = b["bb_total"] * rng.uniform(0.85, 0.95, n)
    b["tr_total"] = pop * 0.48
    b["tr_transit"] = b["tr_total"] * rng.uniform(0.005, 0.12, n)
    b["veh_total"] = b["ten_total"]
    b["veh_none"] = b["veh_total"] * rng.uniform(0.03, 0.12, n)
    b["tt_workers"] = b["tr_total"] * 0.85
    b["tt_agg"] = b["tt_workers"] * rng.uniform(22, 34, n)
    b["tax_med"] = rng.uniform(1500, 9000, n)
    b["ind_total"] = pop * 0.47
    ind = rng.dirichlet(np.ones(13) * 4, n)
    for j in range(13):
        b[f"ind_C24050_{j + 2:03d}E"] = b["ind_total"] * ind[:, j]
    b["yb_total"] = b["units_total"]
    b["yb_2020p"] = b["yb_total"] * rng.uniform(0.02, 0.08, n)
    b["yb_2010s"] = b["yb_total"] * rng.uniform(0.05, 0.2, n)
    b["yb_pre1960"] = b["yb_total"] * rng.uniform(0.05, 0.35, n)
    b["us_total"] = b["units_total"]
    b["us_5plus"] = b["us_total"] * rng.uniform(0.1, 0.35, n)
    for c in list(b.columns):
        if c not in ("name", "pop_cty_now", "pop_cty_old"):
            b[c + "_moe"] = b[c].abs() * 0.02
    b["hmda_c1"] = rng.integers(5000, 60000, n)
    b["hmda_c2"] = (b["hmda_c1"] * 0.03).astype(int)
    b["hmda_c3"] = (b["hmda_c1"] * rng.uniform(0.06, 0.15, n)).astype(int)
    return b


# ----------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", help="case-study metro (part of its name, e.g. 'Denver')")
    ap.add_argument("--demo", action="store_true", help="synthetic data to test the code (NOT real)")
    ap.add_argument("--runs", type=int, default=MC_RUNS)
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    if args.demo:
        log("*** DEMO MODE: SYNTHETIC DATA. NOTHING HERE IS REAL. ***")
        base = demo_base()
    else:
        base, _ = build_base(census_key())

    m = compute_metrics(base)
    issues = validate(base, m)
    for x in issues:
        log(f"VALIDATION: {x}")

    s, aff, acc, peer_med, gap, idx, D = peers_and_gap(m)
    res = m.copy()
    res.insert(0, "name", base["name"])
    res.insert(1, "short", base["name"].map(short_name))
    res["score"], res["affordability_score"], res["access_score"] = s, aff, acc
    res["peer_median_score"], res["gap"] = peer_med, gap
    res["gap_rank"] = gap.rank(ascending=False, method="min").astype(int)
    res["twins"] = [" | ".join(res["short"].iloc[row]) for row in idx]
    res = res.join(driver_table(m, idx))
    drv_cols = ["drv_" + c for c, _, _, _ in DRIVERS]
    letters = {"drv_" + c: letter for c, _, letter, _ in DRIVERS}
    top = res[drv_cols].fillna(-np.inf).idxmax(axis=1)
    res["top_driver"] = top.str.replace("drv_", "")
    res["top_driver_home"] = top.map(letters)

    log(f"Running Monte Carlo ({args.runs:,} runs)...")
    mc = monte_carlo(base, args.runs)

    case_id = None
    if args.case:
        hits = res.index[res["name"].str.contains(args.case, case=False)]
        if len(hits) != 1:
            log(f"--case '{args.case}' matched {len(hits)} metros: {res.loc[hits, 'name'].tolist()}")
        else:
            case_id = hits[0]
            cmp = pd.DataFrame({"metric": AFF + ACC,
                                "metro": [res.loc[case_id, c] for c in AFF + ACC],
                                "twin_median": [np.median(m[c].values[idx[res.index.get_loc(case_id)]])
                                                for c in AFF + ACC]})
            cmp.to_csv(OUT / "case_study_table.csv", index=False)

    summarize(res, mc, case_id)
    make_charts(res, mc, case_id)
    write_outputs(base, m, res, mc, idx, D, issues, case_id)
    print(f"\nDone. Open {OUT}/validation_report.txt first, then metros.csv and charts/.")


if __name__ == "__main__":
    main()
