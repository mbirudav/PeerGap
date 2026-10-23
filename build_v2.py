#!/usr/bin/env python3
"""
PeerGap v2 builder - adds the layers the final deck pitches on top of the
existing peergap.py outputs WITHOUT touching them.

Reads:  data/metros.csv, data/peers.csv, data/results.json, data/2023_Gaz_cbsa_national.txt
Writes: outputs/v2/*.csv and data/v2.json (inlined into index.html / forecast.html)

Flags (all run with --all):
  --peers "Miami"    peer table for any metro (default Miami)
  --clusters         K-means archetypes on the matching factors (REAL)
  --divergence       gap change 2015-19 -> 2020-24 (ILLUSTRATIVE until the 2015-19 pull)
  --hmda-reasons     Miami denial reasons + PNC impact (ILLUSTRATIVE until the loan-level pull)
  --scenarios        2026-2036 what-if bands for the live tool (SCENARIO, not a prediction)
  --returns          shared-equity return calculator (REAL arithmetic)

Every illustrative column carries _assumption or _scenario in its name.
Seed 2026 everywhere, matching the existing Monte Carlo.
"""
import argparse, json, sys
from pathlib import Path
import numpy as np, pandas as pd

SEED = 2026
ROOT = Path(__file__).resolve().parent
DATA, OUT = ROOT / "data", ROOT / "outputs" / "v2"
OUT.mkdir(parents=True, exist_ok=True)
ID = "metropolitan statistical area/micropolitan statistical area"

# ---- config: judgment calls, printed on every run --------------------------
CFG = dict(
    gap_metro_sd=0.5,          # "less affordable than its twins" = gap >= 0.5 SD (33 of 100)
    wide_margin_sd=1.5,        # "trails by a wide margin" = gap >= 1.5 SD (6)
    robust_p10=0.0,            # robust = 10th-percentile simulated gap still > 0
    diverging_sd=0.25,         # gap_change >= +0.25 SD
    recovering_sd=-0.25,       # gap_change <= -0.25 SD
    chronic_gap_sd=0.5,        # not moving, but gap >= 0.5
    priority_mc=0.70,          # top-10 in >= 70% of Monte Carlo runs
    condo_rescue_pts=5.0,      # collateral denial share >= 5 pts above peers
    years=list(range(2026, 2037)),
    stretched_share=0.60,      # "6 in 10 renters stretched"
    bank_play_closes=0.5,      # bank play closes half the denial gap with twins by 2031
    boom_h_relief=0.05,        # building boom: H-led gap metros close 0.05 SD of gap per year
)
ROUTES = {
    "H": {"cause": "Housing supply",        "lane": "Build",    "products": ["Hidden-land finder + construction financing", "Predevelopment financing"]},
    "O": {"cause": "Ownership access",      "lane": "Access",   "products": ["Gap Equity Loan", "Second-look underwriting", "Condo rescue loans"]},
    "M": {"cause": "Maintaining stock",     "lane": "Preserve", "products": ["Fix-to-keep loans", "Expiring-affordability alerts"]},
    "E": {"cause": "Expenses of ownership", "lane": "Relief",   "products": ["Homestead auto-filing", "Insurance-retrofit grants", "Escrow early warning"]},
}
WHATIFS = {   # name -> (label, burden drift multiplier, gap carry = share of the last 5-yr gap change that repeats per 5 yrs)
    "rents_outpace_pay":("Rents outpace pay",       1.0, 0.1),   # national shock: twins get squeezed too, so relative gaps mostly hold
    "trend":            ("Current trends continue", 0.6, 0.5),
    "building_boom":    ("Building boom",          -0.35, 0.3),  # plus supply relief for H-led gap metros (CFG boom_h_relief)
}
INDUSTRY = {  # C24050 columns -> short label
    "share_C24050_002E": "Agriculture & mining", "share_C24050_003E": "Construction", "share_C24050_004E": "Manufacturing",
    "share_C24050_005E": "Wholesale", "share_C24050_006E": "Retail", "share_C24050_007E": "Transport & utilities",
    "share_C24050_008E": "Information", "share_C24050_009E": "Finance & real estate", "share_C24050_010E": "Professional services",
    "share_C24050_011E": "Education & health", "share_C24050_012E": "Leisure & hospitality", "share_C24050_013E": "Other services",
    "share_C24050_014E": "Public administration",
}
GROUPS = {  # the four matching groups, equal weight, NO housing costs
    "size_growth": ["log_pop", "pop_growth"], "income": ["med_hh_income"],
    "jobs": list(INDUSTRY), "infrastructure": ["broadband", "transit_share", "no_vehicle", "commute_min"],
}
FACTORS = [c for g in GROUPS.values() for c in g]

def log(s): print(s, flush=True)

def load():
    m = pd.read_csv(DATA / "metros.csv").set_index(ID)
    m.index = m.index.astype(str)
    p = pd.read_csv(DATA / "peers.csv")
    r = json.loads((DATA / "results.json").read_text())
    return m, p, r

def zmat(m):
    X = m[FACTORS].copy()
    X["pop_growth"] = X["pop_growth"].fillna(X["pop_growth"].mean())  # CT boundary change -> average, as in peergap.py
    Z = (X - X.mean()) / X.std(ddof=0)
    for cols in GROUPS.values():                       # equal group weights
        Z[cols] = Z[cols] / np.sqrt(len(cols))
    return Z

# ---- coordinates (REAL, Census 2023 gazetteer) ------------------------------
def coords(m):
    g = pd.read_csv(DATA / "2023_Gaz_cbsa_national.txt", sep="\t", dtype={"GEOID": str})
    g.columns = [c.strip() for c in g.columns]
    g = g.set_index("GEOID")[["INTPTLAT", "INTPTLONG"]].rename(columns={"INTPTLAT": "lat", "INTPTLONG": "lon"})
    c = m[["short", "name"]].join(g, how="left")
    missing = c.index[c.lat.isna()].tolist()
    if missing: sys.exit(f"GATE FAIL: no gazetteer match for {missing}")
    c.to_csv(OUT / "metro_coords.csv", index_label="cbsa")
    log(f"coords: {len(c)}/{len(m)} metros matched to the 2023 gazetteer")
    return c

# ---- peers table (REAL) -----------------------------------------------------
def peers_table(m, p, query):
    hits = m[m.name.str.contains(query, case=False)]
    if len(hits) != 1: sys.exit(f"--peers '{query}' matched {len(hits)} metros: {hits.short.tolist()}")
    cid = hits.index[0]; me = m.loc[cid]
    tw = p[p.metro == me["name"]].sort_values("rank")
    name2id = {v: k for k, v in m.name.items()}
    rows = []
    def row(r, role, dist):
        top = r[list(INDUSTRY)].astype(float).idxmax()
        return dict(role=role, metro=r["short"], distance=dist, population=int(round(np.exp(r.log_pop))),
                    pop_growth_5yr=r.pop_growth, median_hh_income=r.med_hh_income,
                    top_industry=INDUSTRY[top], top_industry_share=r[top], broadband=r.broadband,
                    transit_share=r.transit_share, no_vehicle=r.no_vehicle, commute_min=r.commute_min,
                    housing_score=r.score, gap=r.gap)
    rows.append(row(me, "self", 0.0))
    for _, t in tw.iterrows(): rows.append(row(m.loc[name2id[t.twin]], "twin", t.distance))
    df = pd.DataFrame(rows)
    med = df[df.role == "twin"].median(numeric_only=True).astype(object); med["role"], med["metro"] = "twin_median", "Twin median"
    med["top_industry"] = df[df.role == "twin"].top_industry.mode()[0]
    df = pd.concat([df, med.to_frame().T], ignore_index=True)
    fn = OUT / f"{me['short'].lower().replace(' ', '_')}_peers.csv"
    df.to_csv(fn, index=False); log(f"peers: wrote {fn.name} ({len(tw)} twins)")
    return df

# ---- clusters (REAL) ---------------------------------------------------------
def clusters(m, p):
    from sklearn.cluster import KMeans
    from sklearn.metrics import silhouette_score, silhouette_samples
    from sklearn.decomposition import PCA
    Z = zmat(m); best = None
    for k in range(4, 9):
        km = KMeans(n_clusters=k, n_init=50, random_state=SEED).fit(Z)
        s = silhouette_score(Z, km.labels_); smallest = np.bincount(km.labels_).min()
        log(f"clusters: k={k} silhouette={s:.3f} smallest cluster={smallest}")
        if best is None or s > best[1]: best = (k, s, km)
    k, s, km = best; log(f"clusters: chosen k={k} (silhouette {s:.3f}; ~0.2 is normal for cities)")
    labels = km.labels_.copy()
    for c in np.where(np.bincount(labels) < 3)[0]:          # fold tiny clusters (New York is a size outlier) into the nearest big one
        members = np.where(labels == c)[0]; others = [o for o in range(k) if np.sum(labels == o) >= 3]
        for i in members:
            labels[i] = min(others, key=lambda o: np.linalg.norm(Z.values[i] - km.cluster_centers_[o]))
        log(f"clusters: cluster {c} had {len(members)} metro(s) ({', '.join(m.short.iloc[members])}); folded into nearest archetype")
    labels = pd.factorize(labels, sort=True)[0]
    lab = pd.Series(labels, index=m.index, name="cluster")
    sil = pd.Series(silhouette_samples(Z, labels), index=m.index, name="silhouette")
    name2id = {v: k2 for k2, v in m.name.items()}
    cons = []
    for cid in m.index:
        tw = p[p.metro == m.loc[cid, "name"]].twin.map(name2id)
        cons.append((lab[tw] == lab[cid]).mean())
    cons = pd.Series(cons, index=m.index, name="peer_consistency")
    log(f"clusters: average share of a metro's 10 twins in its own cluster = {cons.mean():.2f}")
    pc = PCA(2, random_state=SEED).fit_transform(Z)
    out = pd.DataFrame({"metro": m.short, "cluster": lab, "silhouette": sil.round(3), "peer_consistency": cons.round(2),
                        "pca_x": pc[:, 0].round(3), "pca_y": pc[:, 1].round(3)}, index=m.index)
    out.to_csv(OUT / "clusters.csv", index_label="cbsa")
    gapm = m.gap >= CFG["gap_metro_sd"]; prio = gapm & (m.mc_top10_share >= CFG["priority_mc"])
    prof = m.groupby(lab)[FACTORS].mean()
    prof.insert(0, "n_metros", lab.value_counts().sort_index())
    prof.insert(1, "n_gap_metros", gapm.groupby(lab).sum())
    prof.insert(2, "top_home_cause", m[gapm].groupby(lab).top_driver_home.agg(lambda s: s.mode()[0] if len(s) else ""))
    prof.insert(3, "priority_markets", m[prio].groupby(lab).short.agg(" | ".join))
    prof.insert(4, "example_metros", m.assign(c=lab).sort_values("log_pop", ascending=False).groupby("c").short.agg(lambda s: " | ".join(s.head(4))))
    prof["top_home_cause"] = prof.top_home_cause.fillna(""); prof["priority_markets"] = prof.priority_markets.fillna("")
    prof.to_csv(OUT / "cluster_profiles.csv", index_label="cluster")
    names = prof[["n_metros", "n_gap_metros", "top_home_cause", "example_metros"]].copy(); names["name"] = ""
    fn = OUT / "cluster_names.csv"
    if fn.exists():   # keep human-entered names
        old = pd.read_csv(fn, dtype={"name": str}).set_index("cluster"); names["name"] = old.reindex(names.index)["name"].fillna("")
    names.to_csv(fn, index_label="cluster"); log("clusters: wrote clusters.csv, cluster_profiles.csv, cluster_names.csv (name column is for humans)")
    return out, prof, s

# ---- divergence (ILLUSTRATIVE) ----------------------------------------------
def divergence(m):
    rng = np.random.default_rng(SEED)
    def z(s): s = s.fillna(s.mean()); return (s - s.mean()) / s.std(ddof=0)
    # Stand-in for the 2015-19 rebuild: growth pressure, thin new supply and the access gap push gaps up.
    signal = 0.30 * z(m.pop_growth) - 0.30 * z(m.recent_build) + 0.25 * z(m.drv_denial) + 0.15 * z(m.gap)
    change = (0.42 * signal + rng.normal(0, 0.12, len(m))).round(3)
    spread = (m.mc_gap_p90 - m.mc_gap_p10) / 2.56 * 0.5           # half the current MC sd as the noise sd
    draws = change.values[:, None] + rng.normal(0, 1, (len(m), 1000)) * spread.values[:, None]
    def label(gc, g):
        if gc >= CFG["diverging_sd"]: return "diverging"
        if gc <= CFG["recovering_sd"]: return "recovering"
        return "chronic" if g >= CFG["chronic_gap_sd"] else "stable"
    df = pd.DataFrame({"metro": m.short, "gap_2020_24": m.gap.round(3),
                       "gap_2015_19_scenario": (m.gap - change).round(3), "gap_change_scenario": change,
                       "trend_scenario": [label(c, g) for c, g in zip(change, m.gap)],
                       "mc_diverging_share_scenario": (draws >= CFG["diverging_sd"]).mean(1).round(3)}, index=m.index)
    df.to_csv(OUT / "divergence.csv", index_label="cbsa")
    log(f"divergence: thresholds diverging>={CFG['diverging_sd']}, recovering<={CFG['recovering_sd']}, chronic gap>={CFG['chronic_gap_sd']} "
        f"-> {df.trend_scenario.value_counts().to_dict()}  [ILLUSTRATIVE until ACS 2015-19 + HMDA 2019 are pulled]")
    return df

# ---- funnel + routes ---------------------------------------------------------
def funnel(m, div):
    gapm = m[m.gap >= CFG["gap_metro_sd"]]
    robust = gapm[gapm.mc_gap_p10 > CFG["robust_p10"]]
    tr = div.loc[robust.index, "trend_scenario"]
    keep = robust[tr != "recovering"]
    prio = gapm[gapm.mc_top10_share >= CFG["priority_mc"]]
    ptr = div.loc[prio.index, "trend_scenario"].value_counts().to_dict()
    rows = [("all large metros", len(m), "100 largest MSAs"),
            ("gap metros", len(gapm), f"gap >= {CFG['gap_metro_sd']} SD vs twins"),
            ("robust", len(robust), "10th-percentile simulated gap still > 0"),
            ("not recovering (scenario)", len(keep), f"drops recovering metros; split: {tr.value_counts().to_dict()} (illustrative)"),
            ("priority markets", len(prio), f"top-10 in >= {int(CFG['priority_mc']*100)}% of 1,000 runs; trend split {ptr} (illustrative)")]
    df = pd.DataFrame(rows, columns=["stage", "count", "definition"]); df.to_csv(OUT / "funnel.csv", index=False)
    log("funnel: " + " -> ".join(f"{s} {n}" for s, n, _ in rows) + f"   [robust split {tr.value_counts().to_dict()}]"); return df, prio.index.tolist()

def route(row, collateral_gap_pts=None):
    h = row.top_driver_home; r = ROUTES.get(h)
    if not isinstance(h, str) or r is None or row.gap <= 0:
        return dict(route_cause="none", route_lane="Watch", route_products="", route_reason="no driver worse than twins")
    prods = list(r["products"])
    if h == "O" and collateral_gap_pts is not None and collateral_gap_pts >= CFG["condo_rescue_pts"]:
        prods.remove("Condo rescue loans"); prods.insert(0, "Condo rescue loans")
        why = f"collateral denials {collateral_gap_pts:+.1f} pts vs peers -> condo rescue first"
    else:
        why = f"largest driver vs twins: {row.top_driver} ({row['drv_' + row.top_driver]:+.2f} SD)"
    return dict(route_cause=r["cause"], route_lane=r["lane"], route_products=" | ".join(prods), route_reason=why)

def gap_metros(m, div, clus, collateral):
    gm = m[m.gap >= CFG["gap_metro_sd"]].sort_values("gap", ascending=False)
    rows = []
    for cid, r in gm.iterrows():
        d = dict(metro=r.short, gap=round(r.gap, 3), rank=int(r.gap_rank), mc_top10_share=r.mc_top10_share,
                 home=r.top_driver_home, trend_scenario=div.loc[cid, "trend_scenario"],
                 cluster=int(clus.loc[cid, "cluster"]) if clus is not None else "",
                 collateral_gap_pts_assumption=collateral.get(cid, np.nan))
        d.update(route(r, collateral.get(cid))); rows.append(d)
    df = pd.DataFrame(rows, index=gm.index); df.to_csv(OUT / "gap_metros.csv", index_label="cbsa")
    log(f"routes: {len(df)} gap metros -> lanes {df.route_lane.value_counts().to_dict()}"); return df

# ---- HMDA denial reasons + PNC impact (ILLUSTRATIVE) -------------------------
REASONS = {1: "Debt-to-income ratio", 2: "Employment history", 3: "Credit history", 4: "Collateral", 5: "Insufficient cash",
           6: "Unverifiable information", 7: "Incomplete application", 8: "Mortgage insurance denied", 9: "Other", 10: "Not applicable"}
def hmda_reasons(m):
    # PLACEHOLDER shares, not data. Replace with the HMDA 2025 loan-level pull (denial_reason-1, CBSA 33100 + its 10 twins); see CHANGES.md.
    miami = {1: 31.5, 2: 1.6, 3: 17.8, 4: 12.4, 5: 10.9, 6: 5.1, 7: 9.7, 8: 0.6, 9: 8.2, 10: 2.2}
    peer  = {1: 30.1, 2: 1.9, 3: 21.6, 4:  9.3, 5:  7.4, 6: 5.6, 7: 9.9, 8: 0.5, 9: 9.6, 10: 4.1}
    df = pd.DataFrame({"reason_code": list(REASONS), "reason": list(REASONS.values()),
                       "miami_share_pct_assumption": [miami[k] for k in REASONS],
                       "peer_median_share_pct_assumption": [peer[k] for k in REASONS]})
    df["difference_pts_assumption"] = (df.miami_share_pct_assumption - df.peer_median_share_pct_assumption).round(1)
    df.to_csv(OUT / "miami_denial_reasons.csv", index=False)
    coll = float(df.loc[df.reason_code == 4, "difference_pts_assumption"].iloc[0])
    flag = "condo rescue" if coll >= CFG["condo_rescue_pts"] else "gap equity loan"
    log(f"hmda: Miami collateral share {coll:+.1f} pts vs peer median (rule: >= {CFG['condo_rescue_pts']} pts) -> FLAGSHIP: {flag}  [ILLUSTRATIVE]")
    # PNC-only impact. PNC Bank N.A. LEI to be confirmed in the HMDA institution search; denial count is a placeholder.
    pnc_denials = 1240
    fixable_codes = [1, 5, 7] + ([4] if flag == "condo rescue" else [])
    share_fix = df[df.reason_code.isin(fixable_codes)].miami_share_pct_assumption.sum() / 100
    rows = [dict(pnc_miami_denials_assumption=pnc_denials, fixable_codes="|".join(map(str, fixable_codes)),
                 share_fixable_assumption=round(share_fix, 3), reapproval_rate_assumption=r,
                 approvals_gained_scenario=int(round(pnc_denials * share_fix * r))) for r in (0.25, 0.50, 0.75)]
    imp = pd.DataFrame(rows); imp.to_csv(OUT / "pnc_miami_impact.csv", index=False)
    for r in rows:
        log(f"pnc: approvals_gained = {pnc_denials} denials x {share_fix:.3f} fixable x {r['reapproval_rate_assumption']:.2f} reapproval = {r['approvals_gained_scenario']}")
    return df, {"33100": coll}, flag

# ---- scenarios 2026-2036 (SCENARIO) -----------------------------------------
def bank_play_effect(m, p):
    """REAL arithmetic on an assumed play: cut each metro's denial gap with its twins by CFG bank_play_closes,
    re-score with the baseline standardization held fixed (twins unchanged), return the drop in gap (SD).
    score = z(0.5*mean(z(aff measures)) + 0.5*(0.5*z(denial) - 0.5*z(homeownership)))  -- same as peergap.score()"""
    z = lambda x: (x - x.mean()) / x.std(ddof=0)
    aff = sum(z(m[c]) for c in ["burden", "severe", "rent_to_income", "price_to_income"]) / 4
    acc = 0.5 * z(m.denial) - 0.5 * z(m.homeownership)
    comp = 0.5 * aff + 0.5 * acc
    assert np.allclose(z(comp), m.score), "score formula drifted from peergap.py"
    den = dict(zip(m.name, m.denial))
    twin_den = m.name.map(p.groupby("metro").twin.apply(lambda t: np.median([den[x] for x in t])))
    cut = CFG["bank_play_closes"] * (m.denial - twin_den).clip(lower=0)
    return 0.5 * 0.5 * cut / m.denial.std(ddof=0) / comp.std(ddof=0), twin_den

def scenarios(m, p, div):
    yrs = CFG["years"]; T = np.array(yrs) - yrs[0]
    pressure = np.clip((m.gap + 1) / 3, 0, 1)                       # 0..1, Miami ~1
    base_drift = 1.0 + 1.0 * pressure                                # pts of renter burden per year under "rents outpace pay"
    play, twin_den = bank_play_effect(m, p)
    ramp = np.minimum(T / 5, 1)                                      # play fully in effect by 2031
    rows, blob = [], {}
    for cid, r in m.iterrows():
        blob[cid] = {}
        for w, (label, bmult, carry) in WHATIFS.items():
            burden = r.burden + base_drift[cid] * bmult * T / 100
            gap = r.gap + div.loc[cid, "gap_change_scenario"] * carry * T / 5
            if w == "building_boom" and r.top_driver_home == "H" and r.gap > 0:
                gap = gap - CFG["boom_h_relief"] * T
            gap_play = gap - play[cid] * ramp
            width = (r.mc_gap_p90 - r.mc_gap_p10) / 2 * (1 + 0.08 * T)
            p10, p90 = gap - width, gap + width
            bw = 0.02 * (1 + 0.15 * T)
            blob[cid][w] = dict(b=np.round(burden, 4).tolist(), g=np.round(gap, 3).tolist(), gp=np.round(gap_play, 3).tolist(),
                                lo=np.round(p10, 3).tolist(), hi=np.round(p90, 3).tolist(), blo=np.round(burden - bw, 4).tolist(), bhi=np.round(burden + bw, 4).tolist())
            for i, y in enumerate(yrs):
                rows.append(dict(cbsa=cid, metro=r.short, whatif=w, year=y, burden_scenario=round(burden[i], 4),
                                 gap_scenario=round(gap[i], 3), gap_with_bank_play_scenario=round(gap_play[i], 3),
                                 gap_p10_scenario=round(p10[i], 3), gap_p90_scenario=round(p90[i], 3)))
    df = pd.DataFrame(rows)
    df["rank_scenario"] = df.groupby(["whatif", "year"]).gap_scenario.rank(ascending=False, method="min").astype(int)
    df.to_csv(OUT / "scenarios.csv", index=False)
    mi = df[(df.metro == "Miami") & (df.whatif == "rents_outpace_pay") & (df.year.isin([2026, 2031]))]
    n26 = (df[(df.whatif == "rents_outpace_pay") & (df.year == 2026)].burden_scenario >= CFG["stretched_share"]).sum()
    n31 = (df[(df.whatif == "rents_outpace_pay") & (df.year == 2031)].burden_scenario >= CFG["stretched_share"]).sum()
    g31, gp31 = mi.gap_scenario.iloc[1], mi.gap_with_bank_play_scenario.iloc[1]
    log(f"scenarios: Miami burden {mi.burden_scenario.iloc[0]:.0%} -> {mi.burden_scenario.iloc[1]:.0%} by 2031 under rents-outpace-pay, "
        f"rank #{mi.rank_scenario.iloc[0]} -> #{mi.rank_scenario.iloc[1]}; metros with >=60% stretched {n26} -> {n31}  [SCENARIO, not a prediction]")
    mrow = m[m.short == "Miami"].iloc[0]
    log(f"bank play (real re-score, assumed play): Miami denial {mrow.denial:.1%} vs twins {twin_den[m.short == 'Miami'].iloc[0]:.1%}; "
        f"closing half cuts the gap {play[m.short == 'Miami'].iloc[0]:.2f} SD -> {(g31 - gp31) / g31:.0%} smaller by 2031")
    return df, blob

# ---- shared-equity returns (REAL arithmetic) --------------------------------
def investor_return(price, share=0.20, appreciation_share=0.20, growth=0.0, years=10):
    invested = price * share
    repaid = invested + price * ((1 + growth) ** years - 1) * appreciation_share
    annual = (repaid / invested) ** (1 / years) - 1
    return repaid, annual

def returns(price=443_300):
    cases = [(0.00, .20), (0.03, .20), (0.045, .20), (0.05, .20), (0.05, .15), (-0.0105, .20)]  # last: prices fall 10% over 10 yrs
    rows = []
    for g, sh in cases:
        rep, ann = investor_return(price, 0.20, sh, g, 10)
        rows.append(dict(growth_per_yr=g, share_kept_by_pool=sh, home_price=price, repaid=round(rep), return_per_yr=round(ann, 4),
                         scenario="prices fall 10% over 10 yrs" if g < 0 else ""))
    df = pd.DataFrame(rows); df.to_csv(OUT / "shared_equity_returns.csv", index=False)
    pool = 100 * 0.20 * price
    log(f"returns: 100-loan Miami pilot pool = 100 x 20% x ${price:,} = ${pool:,.0f}")
    expect = {(0.0, .2): 88660, (0.03, .2): 119152, (0.045, .2): 137686, (0.05, .2): 144418, (0.05, .15): 130478}
    for (g, sh), v in expect.items():
        got = round(investor_return(price, .2, sh, g)[0])
        if abs(got - v) > 3: sys.exit(f"GATE FAIL: shared-equity {g},{sh} -> {got} != {v}")
    log("returns: 5-row table reproduces the deck to the dollar"); return df

# ---- v2.json for the pages ---------------------------------------------------
def write_json(m, c, clus, prof, div, gm, blob, flag, denial_df, prio, p):
    dist = {cid: p[p.metro == m.loc[cid, "name"]].sort_values("rank").distance.round(2).tolist() for cid in m.index}
    def factors(r):
        top = r[list(INDUSTRY)].astype(float).idxmax()
        g = None if pd.isna(r.pop_growth) else round(float(r.pop_growth), 4)
        return [int(round(np.exp(r.log_pop))), g, int(r.med_hh_income), INDUSTRY[top], round(float(r[top]), 4),
                round(float(r.broadband), 4), round(float(r.transit_share), 4), round(float(r.no_vehicle), 4), round(float(r.commute_min), 1), round(float(r.score), 3)]
    names = pd.read_csv(OUT / "cluster_names.csv", dtype={"name": str}).set_index("cluster")["name"].fillna("").to_dict()
    metros = {}
    for cid, r in m.iterrows():
        k = int(clus.loc[cid, "cluster"]); rt = gm.loc[cid].to_dict() if cid in gm.index else route(r)
        metros[cid] = dict(lat=round(c.loc[cid, "lat"], 4), lon=round(c.loc[cid, "lon"], 4), cluster=k,
                           cluster_name=names.get(k, "") or f"Archetype {k + 1}",
                           trend=div.loc[cid, "trend_scenario"], gap_change=float(div.loc[cid, "gap_change_scenario"]),
                           gap_then=float(div.loc[cid, "gap_2015_19_scenario"]), diverging_share=float(div.loc[cid, "mc_diverging_share_scenario"]),
                           lane=rt["route_lane"], products=[p for p in str(rt["route_products"]).split(" | ") if p], route_reason=rt["route_reason"],
                           priority=cid in prio, f=factors(r), td=dist[cid], sc=blob[cid])
    out = dict(cfg={k: v for k, v in CFG.items() if k != "years"}, years=CFG["years"],
               whatifs={k: v[0] for k, v in WHATIFS.items()}, routes=ROUTES, flagship=flag,
               clusters={int(i): dict(n=int(p.n_metros), gap_n=int(p.n_gap_metros), cause=p.top_home_cause, examples=p.example_metros,
                                      name=names.get(int(i), "") or f"Archetype {int(i) + 1}") for i, p in prof.iterrows()},
               miami_denial_reasons=denial_df.to_dict("records"), metros=metros)
    (DATA / "v2.json").write_text(json.dumps(out, separators=(",", ":")))
    log(f"json: wrote data/v2.json ({(DATA / 'v2.json').stat().st_size // 1024} KB)")

# ---- charts ------------------------------------------------------------------
def charts(m, clus, div, denial_df, prio):
    import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
    BG, INK, MUTED, GREEN, ORANGE, RED, DOT = "#FAF9F6", "#1D2125", "#5E6670", "#08804F", "#EF6A00", "#C41230", "#CFC9BE"
    plt.rcParams.update({"figure.facecolor": BG, "axes.facecolor": BG, "axes.edgecolor": MUTED, "text.color": INK,
                         "axes.labelcolor": INK, "xtick.color": MUTED, "ytick.color": MUTED, "font.size": 11,
                         "axes.spines.top": False, "axes.spines.right": False})
    # 1) Miami denial reasons vs twins (ASSUMPTION data)
    d = denial_df[denial_df.reason_code != 10].sort_values("miami_share_pct_assumption")
    fig, ax = plt.subplots(figsize=(9, 5.2)); y = np.arange(len(d))
    ax.barh(y + 0.2, d.miami_share_pct_assumption, 0.38, color=ORANGE, label="Miami")
    ax.barh(y - 0.2, d.peer_median_share_pct_assumption, 0.38, color=DOT, label="Twin median")
    ax.set_yticks(y, d.reason); ax.set_xlabel("Share of denials, primary reason (%)"); ax.legend(frameon=False, loc="lower right")
    ax.set_title("Why Miami buyers are denied vs. its twins  [assumption: placeholder until the HMDA loan-level pull]", fontsize=11, loc="left", color=MUTED)
    fig.tight_layout(); fig.savefig(OUT / "miami_denial_reasons.png", dpi=200); plt.close(fig)
    # 2) divergence quadrant (SCENARIO data)
    fig, ax = plt.subplots(figsize=(9, 6))
    col = div.trend_scenario.map({"diverging": RED, "chronic": ORANGE, "recovering": GREEN, "stable": DOT})
    ax.scatter(div.gap_2020_24, div.gap_change_scenario, c=col, s=34, edgecolor="white", linewidth=0.6, zorder=3)
    for t in (CFG["diverging_sd"], CFG["recovering_sd"]): ax.axhline(t, color=MUTED, ls="--", lw=0.8)
    ax.axvline(CFG["chronic_gap_sd"], color=MUTED, ls="--", lw=0.8)
    for cid in prio:
        ax.annotate(m.loc[cid, "short"], (div.loc[cid, "gap_2020_24"], div.loc[cid, "gap_change_scenario"]), xytext=(6, 4), textcoords="offset points", fontsize=10, fontweight="bold")
    ax.set_xlabel("Gap vs. twins, 2020-24 (SD)"); ax.set_ylabel("Gap change since 2015-19 (SD)")
    ax.set_title("Is the gap growing?  [scenario: illustrative until the ACS 2015-19 rebuild]", fontsize=11, loc="left", color=MUTED)
    for txt, xy, c in [("Diverging", (0.98, 0.97), RED), ("Recovering", (0.98, 0.03), GREEN)]:
        ax.text(*xy, txt, transform=ax.transAxes, ha="right", va="top" if xy[1] > 0.5 else "bottom", color=c, fontweight="bold")
    fig.tight_layout(); fig.savefig(OUT / "divergence_quadrant.png", dpi=200); plt.close(fig)
    # 3) archetype map (REAL)
    fig, ax = plt.subplots(figsize=(9, 6)); pal = [ORANGE, GREEN, "#3B6EA8", "#8A5A9E", "#B08900", RED, MUTED, INK]
    gap = m.gap >= CFG["gap_metro_sd"]; size = 12 + 60 * (m.log_pop - m.log_pop.min()) / (m.log_pop.max() - m.log_pop.min())
    for k in sorted(clus.cluster.unique()):
        sel = clus.cluster == k
        ax.scatter(clus.pca_x[sel], clus.pca_y[sel], s=size[sel], color=pal[k], alpha=0.85, label=f"Archetype {k + 1}",
                   edgecolor=np.where(gap[sel], INK, "white"), linewidth=np.where(gap[sel], 1.4, 0.5), zorder=3)
    for j, cid in enumerate(sorted(prio, key=lambda c: clus.loc[c, "pca_y"])):
        ax.annotate(m.loc[cid, "short"].replace("Urban ", ""), (clus.loc[cid, "pca_x"], clus.loc[cid, "pca_y"]), xytext=(7, -13 if j % 2 else 5), textcoords="offset points", fontsize=10, fontweight="bold")
    ax.set_xlabel("PCA 1 of the matching factors"); ax.set_ylabel("PCA 2"); ax.legend(frameon=False, fontsize=10)
    ax.set_title("Market archetypes (K-means on the twin-matching factors; dark outline = gap metro)", fontsize=11, loc="left", color=MUTED)
    fig.tight_layout(); fig.savefig(OUT / "archetype_map.png", dpi=200); plt.close(fig)
    log("charts: wrote miami_denial_reasons.png, divergence_quadrant.png, archetype_map.png")

def main():
    ap = argparse.ArgumentParser()
    for f in ["clusters", "divergence", "hmda_reasons", "scenarios", "returns", "all"]: ap.add_argument("--" + f.replace("_", "-"), action="store_true")
    ap.add_argument("--peers", default=None); a = ap.parse_args()
    if not any(vars(a).values()): a.all = True
    log("config: " + ", ".join(f"{k}={v}" for k, v in CFG.items() if k != "years"))
    m, p, r = load(); c = coords(m)
    if a.peers or a.all: peers_table(m, p, a.peers or "Miami")
    if a.returns or a.all: returns()
    if not a.all and not (a.clusters or a.divergence or a.hmda_reasons or a.scenarios): return
    clus, prof, sil = clusters(m, p)
    div = divergence(m)
    denial_df, coll, flag = hmda_reasons(m)
    gm = gap_metros(m, div, clus, coll)
    _, prio = funnel(m, div)
    _, blob = scenarios(m, p, div)
    charts(m, clus, div, denial_df, prio)
    write_json(m, c, clus, prof, div, gm, blob, flag, denial_df, prio, p)
    log("done.")

if __name__ == "__main__":
    main()
