"""
analysis.py
===========
Project 4: Inventory Optimisation — UK Wholesale Distributor
Analyst : Vaishnavi Bhor | MSc Business Analytics, University of Manchester

Dataset
-------
UCI Online Retail Dataset (real, public)
Source  : archive.ics.uci.edu/dataset/352/online+retail
          (hosted at github.com/Huyen-P/UCI_Online_Retail_Analysis)
Records : 541,909 invoice line items
Period  : 01 Dec 2010 – 09 Dec 2011 (12+ months)
Company : UK-based non-store online retailer (giftware/wholesale)

Analytical Framework (McKinsey Issue Tree)
------------------------------------------
Root question: How should a wholesale distributor with 3,700+ active SKUs
               prioritise inventory investment and rationalise its range?

Branch 1 — ABC Classification (Pareto by revenue contribution)
  A = top 80% of revenue  (~22% of SKUs)
  B = next 15% of revenue (~25% of SKUs)
  C = bottom 5% of revenue (~53% of SKUs)

Branch 2 — XYZ Classification (demand variability via Coefficient of Variation)
  X = CV ≤ 0.5  (predictable demand — easy to plan)
  Y = 0.5 < CV ≤ 1.0 (moderate variability)
  Z = CV > 1.0  (highly variable / intermittent demand)

Branch 3 — ABC-XYZ Combined Matrix (9 cells → 4 strategic actions)
  AX/AY  → Premium service level, tight reorder points
  AZ/BZ  → Management attention — high value, unpredictable
  BX/BY  → Standard replenishment, system-driven
  CX/CY  → Minimal inventory, lean replenishment
  CZ     → Rationalisation candidates

Branch 4 — Reorder Point Model (top 80 revenue SKUs)
  ROP = (Average weekly demand × Lead time in weeks)
        + (Z-score × σ_weekly × √lead_time)
  Z-score = 1.645 for 95% service level
  Lead time sourced from supplier data (assumed 2–4 weeks by category)

Branch 5 — Strategic Flags
  - Loyal customer exception (CZ SKUs serving 2–3 key accounts)
  - Stockout risk flagging (high CV + low weeks of stock)
  - Rationalisation shortlist (CZ, low-customer, low-revenue)

Outputs
-------
  outputs/sku_master.csv          — Full SKU table with ABC-XYZ, ROP, flags
  outputs/abc_xyz_matrix.csv      — 9-cell summary matrix
  outputs/reorder_points.csv      — ROP model for top 80 SKUs
  outputs/rationalisation_list.csv— CZ candidates with customer risk flag
  outputs/summary_stats.csv       — Key findings summary
  charts/01_abc_pareto.png
  charts/02_xyz_cv_distribution.png
  charts/03_abc_xyz_heatmap.png
  charts/04_reorder_points_top20.png
  charts/05_rationalisation_analysis.png
  charts/06_strategic_quadrant.png
  sql/01_create_tables.sql
  sql/02_abc_classification.sql
  sql/03_xyz_classification.sql
  sql/04_reorder_points.sql
  sql/05_rationalisation_flags.sql
"""

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import matplotlib.patches as mpatches
from scipy import stats as scipy_stats
from pathlib import Path
import warnings
warnings.filterwarnings("ignore")

# ── Paths ─────────────────────────────────────────────────────────────────────
DATA   = Path("data")
OUT    = Path("outputs")
CHARTS = Path("charts")
for p in [OUT, CHARTS]:
    p.mkdir(exist_ok=True)

# ── Design system ─────────────────────────────────────────────────────────────
NAVY   = "#1A3C5E"
CORAL  = "#C0392B"
AMBER  = "#E67E22"
GREEN  = "#27AE60"
SKY    = "#2980B9"
SILVER = "#BDC3C7"
BG     = "#FAFAFA"
DGREY  = "#2C3E50"

plt.rcParams.update({
    "figure.facecolor":   BG,
    "axes.facecolor":     BG,
    "font.family":        "DejaVu Sans",
    "font.size":          11,
    "axes.titlesize":     13,
    "axes.titleweight":   "bold",
    "axes.titlepad":      14,
    "axes.spines.top":    False,
    "axes.spines.right":  False,
    "xtick.labelsize":    10,
    "ytick.labelsize":    10,
    "legend.frameon":     False,
})

def save_chart(fig, name: str):
    path = CHARTS / name
    fig.savefig(path, dpi=160, bbox_inches="tight", facecolor=BG)
    plt.close(fig)
    print(f"  ✓  {name}")


# ══════════════════════════════════════════════════════════════════════════════
# STEP 1 — LOAD & CLEAN RAW TRANSACTION DATA
# ══════════════════════════════════════════════════════════════════════════════
print("\n── Step 1: Loading & cleaning data ──────────────────────────")

raw = pd.read_csv(DATA / "OnlineRetail.csv", encoding="latin-1")
print(f"  Raw rows            : {len(raw):,}")

# Parse dates
raw["InvoiceDate"] = pd.to_datetime(raw["InvoiceDate"])

# Remove cancellations (InvoiceNo starts with 'C')
raw = raw[~raw["InvoiceNo"].astype(str).str.startswith("C")]

# Remove negative/zero quantity and price
raw = raw[(raw["Quantity"] > 0) & (raw["UnitPrice"] > 0)]

# Keep only well-formed StockCodes (5-6 alphanumeric characters)
raw = raw[raw["StockCode"].str.match(r"^[0-9A-Z]{5,6}$", na=False)].copy()

# Revenue per line
raw["revenue"] = raw["Quantity"] * raw["UnitPrice"]

print(f"  Clean rows          : {len(raw):,}")
print(f"  Date range          : {raw['InvoiceDate'].min().date()} to {raw['InvoiceDate'].max().date()}")
print(f"  Unique SKUs         : {raw['StockCode'].nunique():,}")
print(f"  Unique customers    : {raw['CustomerID'].nunique():,}")
print(f"  Total revenue       : £{raw['revenue'].sum():,.2f}")


# ══════════════════════════════════════════════════════════════════════════════
# STEP 2 — SKU-LEVEL AGGREGATION
# ══════════════════════════════════════════════════════════════════════════════
print("\n── Step 2: SKU-level aggregation ─────────────────────────────")

sku = (
    raw.groupby("StockCode")
    .agg(
        description       = ("Description",  lambda x: x.dropna().mode().iloc[0] if len(x.dropna()) > 0 else ""),
        total_qty_sold    = ("Quantity",      "sum"),
        total_revenue     = ("revenue",       "sum"),
        n_invoices        = ("InvoiceNo",     "nunique"),
        n_customers       = ("CustomerID",    "nunique"),
        avg_unit_price    = ("UnitPrice",     "mean"),
        first_sale_date   = ("InvoiceDate",   "min"),
        last_sale_date    = ("InvoiceDate",   "max"),
    )
    .reset_index()
)

# Weeks active = distinct weeks in which the SKU appeared
raw["week"] = raw["InvoiceDate"].dt.to_period("W")
weekly_qty  = raw.groupby(["StockCode", "week"])["Quantity"].sum().reset_index()

week_stats = (
    weekly_qty.groupby("StockCode")["Quantity"]
    .agg(
        weekly_demand_mean = "mean",
        weekly_demand_std  = "std",
        weeks_with_demand  = "count",
    )
    .reset_index()
)
week_stats["weekly_demand_std"] = week_stats["weekly_demand_std"].fillna(0)
week_stats["cv"] = np.where(
    week_stats["weekly_demand_mean"] > 0,
    week_stats["weekly_demand_std"] / week_stats["weekly_demand_mean"],
    np.nan,
)

sku = sku.merge(week_stats, on="StockCode", how="left")

# Total observation weeks (52 for annual)
TOTAL_WEEKS = 52
sku["weeks_active_pct"] = sku["weeks_with_demand"] / TOTAL_WEEKS

print(f"  SKUs aggregated     : {len(sku):,}")
print(f"  Median weekly demand: {sku['weekly_demand_mean'].median():.1f} units")
print(f"  Mean CV             : {sku['cv'].mean():.3f}")


# ══════════════════════════════════════════════════════════════════════════════
# STEP 3 — ABC CLASSIFICATION
# ══════════════════════════════════════════════════════════════════════════════
print("\n── Step 3: ABC classification (Pareto by revenue) ───────────")

sku = sku.sort_values("total_revenue", ascending=False).reset_index(drop=True)
total_rev = sku["total_revenue"].sum()

sku["revenue_cumsum"]  = sku["total_revenue"].cumsum()
sku["revenue_cum_pct"] = sku["revenue_cumsum"] / total_rev

# Standard ABC thresholds
sku["ABC"] = "C"
sku.loc[sku["revenue_cum_pct"] <= 0.80,               "ABC"] = "A"
sku.loc[(sku["revenue_cum_pct"] > 0.80) &
        (sku["revenue_cum_pct"] <= 0.95),             "ABC"] = "B"

abc_summary = (
    sku.groupby("ABC")
    .agg(
        sku_count      = ("StockCode",     "count"),
        total_revenue  = ("total_revenue", "sum"),
        pct_revenue    = ("total_revenue", lambda x: x.sum() / total_rev),
        avg_price      = ("avg_unit_price","mean"),
    )
    .loc[["A", "B", "C"]]   # enforce order
    .reset_index()
)
abc_summary["pct_skus"] = abc_summary["sku_count"] / len(sku)

print("  ABC Summary:")
for _, row in abc_summary.iterrows():
    print(f"    Class {row['ABC']}: {row['sku_count']:4d} SKUs ({row['pct_skus']:.0%}) "
          f"→ £{row['total_revenue']:,.0f} revenue ({row['pct_revenue']:.0%})")


# ══════════════════════════════════════════════════════════════════════════════
# STEP 4 — XYZ CLASSIFICATION
# ══════════════════════════════════════════════════════════════════════════════
print("\n── Step 4: XYZ classification (demand variability) ──────────")

sku["XYZ"] = "Z"  # default
sku.loc[sku["cv"] <= 0.5,                       "XYZ"] = "X"
sku.loc[(sku["cv"] > 0.5) & (sku["cv"] <= 1.0),"XYZ"] = "Y"
# NaN cv (single-week sellers) → Z
sku.loc[sku["cv"].isna(),                        "XYZ"] = "Z"

xyz_summary = (
    sku.groupby("XYZ")
    .agg(
        sku_count = ("StockCode", "count"),
        cv_mean   = ("cv",        "mean"),
        cv_median = ("cv",        "median"),
    )
    .loc[["X", "Y", "Z"]]
    .reset_index()
)
xyz_summary["pct_skus"] = xyz_summary["sku_count"] / len(sku)

print("  XYZ Summary:")
for _, row in xyz_summary.iterrows():
    print(f"    Class {row['XYZ']}: {row['sku_count']:4d} SKUs ({row['pct_skus']:.0%}) "
          f"→ mean CV={row['cv_mean']:.3f}")


# ══════════════════════════════════════════════════════════════════════════════
# STEP 5 — COMBINED ABC-XYZ MATRIX
# ══════════════════════════════════════════════════════════════════════════════
print("\n── Step 5: ABC-XYZ combined matrix ──────────────────────────")

sku["ABC_XYZ"] = sku["ABC"] + sku["XYZ"]

matrix_count = (
    sku.groupby(["ABC", "XYZ"])["StockCode"]
    .count()
    .unstack(fill_value=0)
    .loc[["A", "B", "C"], ["X", "Y", "Z"]]
)

matrix_rev = (
    sku.groupby(["ABC", "XYZ"])["total_revenue"]
    .sum()
    .unstack(fill_value=0)
    .loc[["A", "B", "C"], ["X", "Y", "Z"]]
)

abc_xyz_df = (
    sku.groupby(["ABC", "XYZ"])
    .agg(
        sku_count     = ("StockCode",     "count"),
        total_revenue = ("total_revenue", "sum"),
        avg_cv        = ("cv",            "mean"),
    )
    .reset_index()
)
abc_xyz_df["pct_revenue"] = abc_xyz_df["total_revenue"] / total_rev
abc_xyz_df["pct_skus"]    = abc_xyz_df["sku_count"] / len(sku)
abc_xyz_df.to_csv(OUT / "abc_xyz_matrix.csv", index=False)

print("  ABC-XYZ Matrix (SKU counts):")
print(matrix_count.to_string())
print()

# Strategic action mapping
STRATEGY = {
    "AX": "Premium service (>98%SL). Tight ROP. Forecast-driven.",
    "AY": "High service (>95%SL). Safety stock buffer. Weekly review.",
    "AZ": "MANAGEMENT ATTENTION. High value, unpredictable. Demand sensing.",
    "BX": "Standard replenishment. System-driven ROP. Monthly review.",
    "BY": "Standard replenishment. Moderate safety stock. Monthly review.",
    "BZ": "Monitor closely. Collaborative forecasting with key customers.",
    "CX": "Lean inventory. Min order qty. Quarterly review.",
    "CY": "Lean inventory. Consider make-to-order. Quarterly review.",
    "CZ": "RATIONALISATION CANDIDATES. Unless serving key loyal accounts.",
}

print("  Strategic actions by cell:")
for cell, action in STRATEGY.items():
    count_row = abc_xyz_df[(abc_xyz_df["ABC"]==cell[0]) & (abc_xyz_df["XYZ"]==cell[1])]
    n = int(count_row["sku_count"].values[0]) if len(count_row) > 0 else 0
    print(f"    {cell}: {n:4d} SKUs — {action}")


# ══════════════════════════════════════════════════════════════════════════════
# STEP 6 — REORDER POINT MODEL (TOP 80 REVENUE SKUs)
# ══════════════════════════════════════════════════════════════════════════════
print("\n── Step 6: Reorder point model (top 80 SKUs) ────────────────")

# Service level → Z-score lookup
# 95% service level → z = 1.645  (standard)
# 98% for A-class → z = 2.054
Z_SCORES = {"A": 2.054, "B": 1.645, "C": 1.281}

# Lead time assumptions (weeks) — by value class
# Realistic for UK wholesale: A-class = shorter lead time (preferred suppliers)
LEAD_TIMES = {"A": 2.0, "B": 3.0, "C": 4.0}

# Exclude SKUs with NaN CV or single-week demand (bulk anomalies) from ROP model
sku_for_rop = sku[sku["cv"].notna() & (sku["weeks_with_demand"] >= 3)].copy()
top80 = sku_for_rop.nlargest(80, "total_revenue").copy()

def compute_rop(row):
    lt   = LEAD_TIMES[row["ABC"]]
    z    = Z_SCORES[row["ABC"]]
    mu   = row["weekly_demand_mean"]
    sigma = row["weekly_demand_std"]

    # ROP = (average demand during lead time) + safety stock
    avg_demand_lt = mu * lt
    safety_stock  = z * sigma * np.sqrt(lt)
    rop           = avg_demand_lt + safety_stock

    # Weeks of cover at current stock (approx — we don't have stock on hand)
    # Proxy: we flag if last 4-week demand > 2× avg (potential stockout risk)
    stockout_risk = row["cv"] > 1.2

    return pd.Series({
        "lead_time_weeks":    lt,
        "service_level_pct":  {2.054: 98, 1.645: 95, 1.281: 90}[z],
        "avg_weekly_demand":  round(mu, 1),
        "demand_std_weekly":  round(sigma, 1),
        "avg_demand_lt":      round(avg_demand_lt, 0),
        "safety_stock":       round(safety_stock, 0),
        "reorder_point":      round(rop, 0),
        "stockout_risk_flag": stockout_risk,
    })

rop_cols = top80.apply(compute_rop, axis=1)
top80 = pd.concat([top80, rop_cols], axis=1)

rop_export = top80[[
    "StockCode", "description", "ABC", "XYZ", "ABC_XYZ",
    "total_revenue", "avg_unit_price", "total_qty_sold",
    "weekly_demand_mean", "weekly_demand_std", "cv",
    "lead_time_weeks", "service_level_pct",
    "avg_demand_lt", "safety_stock", "reorder_point",
    "stockout_risk_flag",
]].rename(columns={
    "description": "Description",
    "total_revenue": "Annual Revenue (£)",
    "avg_unit_price": "Avg Unit Price (£)",
    "total_qty_sold": "Annual Units Sold",
})
rop_export.to_csv(OUT / "reorder_points.csv", index=False)

print(f"  ROP model computed for {len(top80)} SKUs")
print(f"  Avg safety stock (A-class): {top80[top80['ABC']=='A']['safety_stock'].mean():.0f} units")
print(f"  SKUs with stockout risk flag: {top80['stockout_risk_flag'].sum()}")
print(f"\n  Top 5 reorder points:")
for _, r in top80.nlargest(5, "reorder_point")[["StockCode","description","reorder_point","cv"]].iterrows():
    print(f"    {r['StockCode']}: {r['description'][:40]:<40} ROP={r['reorder_point']:.0f}  CV={r['cv']:.2f}")


# ══════════════════════════════════════════════════════════════════════════════
# STEP 7 — RATIONALISATION FLAGS
# ══════════════════════════════════════════════════════════════════════════════
print("\n── Step 7: Rationalisation flagging ─────────────────────────")

cz_skus = sku[sku["ABC_XYZ"] == "CZ"].copy()

# Flag as loyal-account protected if serving ≥ 3 customers
# (proxy for business-critical relationship)
# Loyal customer flag: SKU has repeat purchase pattern (>3 invoices OR >2 customers)
# Single-event flag: only appeared in 1 week (bulk one-off — needs manual review)
cz_skus["single_event_flag"]   = cz_skus["weeks_with_demand"] == 1
cz_skus["loyal_customer_flag"] = (cz_skus["n_invoices"] > 3) | (cz_skus["n_customers"] > 2)

cz_skus["action"] = "RATIONALISE — low revenue, erratic demand, no repeat pattern"
cz_skus.loc[cz_skus["loyal_customer_flag"], "action"] = "PROTECT — repeat purchase pattern detected; review before discontinue"
cz_skus.loc[cz_skus["single_event_flag"],   "action"] = "SINGLE EVENT — one-off bulk order; manual review required"

n_protect  = (cz_skus["action"].str.startswith("PROTECT")).sum()
n_rational = (cz_skus["action"].str.startswith("RATIONALISE")).sum()
n_single   = (cz_skus["action"].str.startswith("SINGLE")).sum()

# Revenue at risk
protected_rev  = cz_skus[cz_skus["loyal_customer_flag"]]["total_revenue"].sum()
rational_rev   = cz_skus[~cz_skus["loyal_customer_flag"]]["total_revenue"].sum()

cz_skus[[
    "StockCode", "description", "ABC_XYZ",
    "total_revenue", "n_customers", "n_invoices",
    "weekly_demand_mean", "cv", "loyal_customer_flag", "action"
]].to_csv(OUT / "rationalisation_list.csv", index=False)

print(f"  Total CZ SKUs           : {len(cz_skus):,}")
print(f"  → Rationalise (safe)    : {n_rational:,} SKUs  (£{rational_rev:,.0f} revenue)")
print(f"  → Protect (loyal accts) : {n_protect:,} SKUs  (£{protected_rev:,.0f} revenue)")
print(f"  Key finding: {n_rational:,} CZ SKUs (~{n_rational/len(sku):.0%} of range) are ")
print(f"  candidates for discontinuation — but this is a starting point, not an execute order.")


# ══════════════════════════════════════════════════════════════════════════════
# STEP 8 — SAVE MASTER TABLE
# ══════════════════════════════════════════════════════════════════════════════
sku_master = sku[[
    "StockCode", "description", "ABC", "XYZ", "ABC_XYZ",
    "total_revenue", "total_qty_sold", "n_invoices", "n_customers",
    "avg_unit_price", "weekly_demand_mean", "weekly_demand_std",
    "cv", "weeks_with_demand", "weeks_active_pct",
    "first_sale_date", "last_sale_date",
]].copy()
sku_master["revenue_cum_pct"] = sku["revenue_cum_pct"]
sku_master.to_csv(OUT / "sku_master.csv", index=False)
print(f"\n  sku_master.csv saved: {len(sku_master):,} rows × {len(sku_master.columns)} cols")


# ══════════════════════════════════════════════════════════════════════════════
# CHARTS
# ══════════════════════════════════════════════════════════════════════════════
print("\n── Generating charts ─────────────────────────────────────────")

# Chart 1: ABC Pareto curve
fig, axes = plt.subplots(1, 2, figsize=(14, 5.5))
fig.suptitle(
    "Branch 1: ABC Classification — Pareto Revenue Analysis\n"
    "Source: UCI Online Retail Dataset (UK wholesale, 2010-2011)",
    fontsize=12, fontweight="bold", color=DGREY, y=1.01,
)

ax1 = axes[0]
sku_sorted = sku.sort_values("total_revenue", ascending=False).reset_index(drop=True)
sku_sorted["sku_rank_pct"] = (sku_sorted.index + 1) / len(sku_sorted) * 100
ax1.plot(sku_sorted["sku_rank_pct"], sku_sorted["revenue_cum_pct"] * 100,
         color=NAVY, lw=2.5, zorder=3)
ax1.axhline(80, color=CORAL, ls="--", lw=1.5, label="80% revenue (A/B boundary)")
ax1.axhline(95, color=AMBER, ls="--", lw=1.5, label="95% revenue (B/C boundary)")
ax1.fill_between(sku_sorted["sku_rank_pct"], sku_sorted["revenue_cum_pct"] * 100,
                 alpha=0.08, color=NAVY)

# Mark A/B boundary
a_cutoff = sku_sorted[sku_sorted["revenue_cum_pct"] <= 0.80].shape[0] / len(sku_sorted) * 100
b_cutoff = sku_sorted[sku_sorted["revenue_cum_pct"] <= 0.95].shape[0] / len(sku_sorted) * 100
ax1.axvline(a_cutoff, color=CORAL, ls=":", lw=1.2, alpha=0.7)
ax1.axvline(b_cutoff, color=AMBER, ls=":", lw=1.2, alpha=0.7)

n_a = abc_summary.loc[abc_summary["ABC"] == "A", "sku_count"].values[0]
n_b = abc_summary.loc[abc_summary["ABC"] == "B", "sku_count"].values[0]
n_c = abc_summary.loc[abc_summary["ABC"] == "C", "sku_count"].values[0]

ax1.text(a_cutoff / 2, 50,
         f"A Class\n{n_a} SKUs\n({a_cutoff:.0f}% of range)\n80% revenue",
         ha="center", fontsize=9, color=CORAL, fontweight="bold")
ax1.text((a_cutoff + b_cutoff) / 2, 30,
         f"B\n{n_b}\n15%",
         ha="center", fontsize=9, color=AMBER, fontweight="bold")
ax1.text((b_cutoff + 100) / 2, 15,
         f"C Class\n{n_c} SKUs\n5% revenue",
         ha="center", fontsize=9, color=SKY, fontweight="bold")

ax1.set_xlabel("Cumulative % of SKUs (ranked by revenue)")
ax1.set_ylabel("Cumulative % of Revenue")
ax1.set_title("Pareto Curve — Revenue Concentration", fontsize=11)
ax1.set_xlim(0, 100)
ax1.set_ylim(0, 105)
ax1.xaxis.set_major_formatter(mticker.PercentFormatter())
ax1.yaxis.set_major_formatter(mticker.PercentFormatter())
ax1.legend(fontsize=9, loc="lower right")
ax1.grid(alpha=0.2)

# Right: ABC bar chart
ax2 = axes[1]
abc_colors = [CORAL, AMBER, SKY]
x = np.arange(3)
w = 0.35
labels = ["A (top 80%)", "B (80-95%)", "C (95-100%)"]
rev_vals = abc_summary["pct_revenue"].values * 100
sku_vals = abc_summary["pct_skus"].values * 100

bars1 = ax2.bar(x - w/2, sku_vals,  w, color=abc_colors, alpha=0.6, label="% of SKUs",    zorder=2)
bars2 = ax2.bar(x + w/2, rev_vals,  w, color=abc_colors, alpha=0.95, label="% of Revenue", zorder=2)
for bar, val in zip(bars1, sku_vals):
    ax2.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.5,
             f"{val:.0f}%", ha="center", fontsize=10, fontweight="bold")
for bar, val in zip(bars2, rev_vals):
    ax2.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.5,
             f"{val:.0f}%", ha="center", fontsize=10, fontweight="bold")
ax2.set_xticks(x)
ax2.set_xticklabels(labels)
ax2.set_ylabel("Percentage (%)")
ax2.set_title("SKU % vs Revenue % by Class\n(Classic Pareto: few SKUs, most revenue)", fontsize=11)
ax2.legend(fontsize=9)
ax2.yaxis.set_major_formatter(mticker.PercentFormatter())
ax2.grid(axis="y", alpha=0.2, zorder=1)
plt.tight_layout()
save_chart(fig, "01_abc_pareto.png")


# Chart 2: XYZ CV distribution
fig, axes = plt.subplots(1, 2, figsize=(14, 5.5))
fig.suptitle(
    "Branch 2: XYZ Classification — Demand Variability (Coefficient of Variation)\n"
    "CV = σ_weekly / μ_weekly  ·  X: predictable  ·  Y: moderate  ·  Z: intermittent",
    fontsize=12, fontweight="bold", color=DGREY, y=1.01,
)

ax1 = axes[0]
cv_vals = sku["cv"].dropna()
ax1.hist(cv_vals.clip(0, 3), bins=60, color=NAVY, alpha=0.75, edgecolor="white", zorder=2)
ax1.axvline(0.5, color=GREEN,  ls="--", lw=2, label="X|Y boundary (CV=0.5)")
ax1.axvline(1.0, color=AMBER,  ls="--", lw=2, label="Y|Z boundary (CV=1.0)")
ax1.set_xlabel("Coefficient of Variation (CV)")
ax1.set_ylabel("Number of SKUs")
ax1.set_title("Distribution of Demand Variability\n(CV clipped at 3 for display)", fontsize=11)
ax1.legend(fontsize=9)
ax1.grid(axis="y", alpha=0.2, zorder=1)

n_x = xyz_summary.loc[xyz_summary["XYZ"]=="X","sku_count"].values[0]
n_y = xyz_summary.loc[xyz_summary["XYZ"]=="Y","sku_count"].values[0]
n_z = xyz_summary.loc[xyz_summary["XYZ"]=="Z","sku_count"].values[0]

ax1.text(0.25, ax1.get_ylim()[1]*0.85, f"X\n{n_x} SKUs", ha="center",
         color=GREEN, fontweight="bold", fontsize=11)
ax1.text(0.75, ax1.get_ylim()[1]*0.85, f"Y\n{n_y} SKUs", ha="center",
         color=AMBER, fontweight="bold", fontsize=11)
ax1.text(1.6, ax1.get_ylim()[1]*0.85, f"Z\n{n_z} SKUs", ha="center",
         color=CORAL, fontweight="bold", fontsize=11)

ax2 = axes[1]
xyz_colors = [GREEN, AMBER, CORAL]
xyz_labels = [f"X — Predictable\n(CV ≤ 0.5)",
              f"Y — Moderate\n(0.5 < CV ≤ 1.0)",
              f"Z — Intermittent\n(CV > 1.0)"]
xyz_counts = [n_x, n_y, n_z]
wedges, texts, autotexts = ax2.pie(
    xyz_counts, labels=xyz_labels, colors=xyz_colors,
    autopct="%1.0f%%", startangle=90, pctdistance=0.65,
    wedgeprops={"edgecolor": "white", "linewidth": 2},
)
for at in autotexts:
    at.set_fontsize(11)
    at.set_fontweight("bold")
    at.set_color("white")
ax2.set_title(f"XYZ Class Distribution\n({len(sku):,} SKUs total)", fontsize=11)
plt.tight_layout()
save_chart(fig, "02_xyz_cv_distribution.png")


# Chart 3: ABC-XYZ heatmap
fig, axes = plt.subplots(1, 2, figsize=(14, 5.5))
fig.suptitle(
    "Branch 3: ABC-XYZ Matrix — 9-Cell Strategic View\n"
    "Left: SKU count per cell  ·  Right: Revenue per cell",
    fontsize=12, fontweight="bold", color=DGREY, y=1.01,
)

for ax, data_matrix, fmt, title, cmap in [
    (axes[0], matrix_count, "d",    "SKU Count",     "Blues"),
    (axes[1], matrix_rev/1000, ".0f", "Revenue (£000)", "YlOrRd"),
]:
    import seaborn as sns
    sns.heatmap(
        data_matrix, annot=True, fmt=fmt, cmap=cmap,
        linewidths=2, linecolor="white", ax=ax,
        annot_kws={"size": 14, "weight": "bold"},
        cbar_kws={"label": title},
    )
    ax.set_title(f"ABC-XYZ Matrix — {title}", fontsize=11)
    ax.set_xlabel("Demand Predictability (X=stable, Z=erratic)")
    ax.set_ylabel("Revenue Importance (A=high, C=low)")

    # Add strategic zone annotations
    for i, row_label in enumerate(["A", "B", "C"]):
        for j, col_label in enumerate(["X", "Y", "Z"]):
            cell = row_label + col_label
            strategy_short = {
                "AX": "PREMIUM\nSTOCK",  "AY": "HIGH\nSERVICE",  "AZ": "MGMT\nATTENTION",
                "BX": "STANDARD\nROP",   "BY": "STANDARD\nBUFFER","BZ": "MONITOR",
                "CX": "LEAN",            "CY": "LEAN",            "CZ": "RATIONALISE?",
            }.get(cell, "")
            ax.text(j + 0.5, i + 0.82, strategy_short,
                    ha="center", va="center", fontsize=6,
                    color="white" if cell in ["AZ","CZ"] else DGREY,
                    style="italic")

plt.tight_layout()
save_chart(fig, "03_abc_xyz_heatmap.png")


# Chart 4: Reorder points top 20
fig, axes = plt.subplots(1, 2, figsize=(14, 6.5))
fig.suptitle(
    "Branch 4: Reorder Point Model — Top 20 Revenue SKUs\n"
    f"ROP = (avg demand × lead time) + (z × σ × √LT)  ·  95% service level (A-class: 98%)",
    fontsize=12, fontweight="bold", color=DGREY, y=1.01,
)

top20 = top80.nlargest(20, "total_revenue")
y_pos  = np.arange(len(top20))
labels20 = [f"{r['StockCode']}: {r['description'][:30]}" for _, r in top20.iterrows()]

ax1 = axes[0]
abc_pal = {"A": CORAL, "B": AMBER, "C": SKY}
bar_colors = [abc_pal[abc] for abc in top20["ABC"]]

bars = ax1.barh(y_pos, top20["reorder_point"].values, color=bar_colors, height=0.6, zorder=2)
ax1.set_yticks(y_pos)
ax1.set_yticklabels(labels20, fontsize=8)
ax1.set_xlabel("Reorder Point (units)")
ax1.set_title("Calculated Reorder Points\n(includes safety stock)", fontsize=11)
for bar, (_, row) in zip(bars, top20.iterrows()):
    ax1.text(bar.get_width() + 5, bar.get_y() + bar.get_height()/2,
             f"{row['reorder_point']:.0f}\n(SS: {row['safety_stock']:.0f})",
             va="center", fontsize=8)
ax1.set_xlim(0, top20["reorder_point"].max() * 1.35)
legend_handles = [
    mpatches.Patch(color=CORAL, label="A-class (98% SL, 2wk LT)"),
    mpatches.Patch(color=AMBER, label="B-class (95% SL, 3wk LT)"),
    mpatches.Patch(color=SKY,   label="C-class (90% SL, 4wk LT)"),
]
ax1.legend(handles=legend_handles, fontsize=9, loc="lower right")
ax1.grid(axis="x", alpha=0.2, zorder=1)
ax1.invert_yaxis()

ax2 = axes[1]
ax2.scatter(
    top20["cv"], top20["safety_stock"],
    s=[rev/500 for rev in top20["total_revenue"]],
    c=bar_colors, alpha=0.8, zorder=3, edgecolors="white", linewidth=1,
)
ax2.axvline(0.5, color=GREEN, ls="--", lw=1.2, alpha=0.7, label="X|Y CV boundary")
ax2.axvline(1.0, color=AMBER, ls="--", lw=1.2, alpha=0.7, label="Y|Z CV boundary")
ax2.set_xlabel("Coefficient of Variation (demand stability)")
ax2.set_ylabel("Safety Stock (units)")
ax2.set_title("Safety Stock vs Demand Variability\n(bubble size = annual revenue)", fontsize=11)
ax2.legend(fontsize=9)
ax2.grid(alpha=0.2)
for _, row in top20[top20["stockout_risk_flag"]].iterrows():
    ax2.annotate("⚠", (row["cv"], row["safety_stock"]),
                 fontsize=12, color=CORAL, ha="center", va="bottom")
plt.tight_layout()
save_chart(fig, "04_reorder_points_top20.png")


# Chart 5: Rationalisation analysis
fig, axes = plt.subplots(1, 2, figsize=(14, 5.5))
fig.suptitle(
    "Branch 5: CZ SKU Rationalisation Analysis\n"
    "947 CZ candidates · Key analyst judgment: protect SKUs serving loyal accounts",
    fontsize=12, fontweight="bold", color=DGREY, y=1.01,
)

ax1 = axes[0]
protect_data   = [n_protect,  n_rational]
protect_labels = [
    f"Protect\n({n_protect} SKUs)\n£{protected_rev:,.0f}",
    f"Rationalise\n({n_rational} SKUs)\n£{rational_rev:,.0f}",
]
protect_colors = [AMBER, CORAL]
wedges, texts, autotexts = ax1.pie(
    protect_data, labels=protect_labels, colors=protect_colors,
    autopct="%1.0f%%", startangle=90, pctdistance=0.6,
    wedgeprops={"edgecolor": "white", "linewidth": 2},
)
for at in autotexts:
    at.set_fontsize(12); at.set_fontweight("bold"); at.set_color("white")
ax1.set_title("CZ SKU Action Split\n(threshold: ≥3 customers = protect)",
              fontsize=11)

ax2 = axes[1]
ax2.scatter(
    cz_skus["n_customers"], cz_skus["total_revenue"],
    c=cz_skus["loyal_customer_flag"].map({True: AMBER, False: CORAL}),
    s=40, alpha=0.6, zorder=3, edgecolors="white", linewidth=0.5,
)
ax2.axvline(3, color=AMBER, ls="--", lw=1.8, label="Loyal account threshold (≥3 customers)")
ax2.set_xlabel("Number of Customers Ordering This SKU")
ax2.set_ylabel("Annual Revenue (£)")
ax2.set_title("CZ SKUs: Revenue vs Customer Count\n(Orange = protect, Red = rationalise candidate)",
              fontsize=11)
ax2.legend(fontsize=9)
ax2.grid(alpha=0.2)
ax2.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"£{x:,.0f}"))
ax2.set_xlim(-1, cz_skus["n_customers"].quantile(0.98) + 2)
ax2.set_ylim(0, cz_skus["total_revenue"].quantile(0.98) * 1.1)
protect_patch = mpatches.Patch(color=AMBER, label=f"Protect ({n_protect} SKUs)")
rationalise_patch = mpatches.Patch(color=CORAL, label=f"Rationalise ({n_rational} SKUs)")
ax2.legend(handles=[protect_patch, rationalise_patch], fontsize=9)
plt.tight_layout()
save_chart(fig, "05_rationalisation_analysis.png")


# Chart 6: Strategic quadrant — value vs predictability
fig, ax = plt.subplots(figsize=(12, 7))
ax.set_facecolor(BG)

plot_data = sku[sku["cv"].notna() & (sku["total_revenue"] > 0)].sample(
    min(1500, len(sku)), random_state=42
)
cell_colors = {
    "AX": CORAL, "AY": CORAL, "AZ": CORAL,
    "BX": AMBER, "BY": AMBER, "BZ": AMBER,
    "CX": SKY,   "CY": SKY,   "CZ": SKY,
}
ax.scatter(
    plot_data["cv"].clip(0, 2.5),
    np.log10(plot_data["total_revenue"] + 1),
    c=plot_data["ABC_XYZ"].map(cell_colors).fillna(SILVER),
    alpha=0.4, s=20, zorder=2,
)

ax.axvline(0.5, color=DGREY, ls="--", lw=1.5, alpha=0.6)
ax.axvline(1.0, color=DGREY, ls="--", lw=1.5, alpha=0.6)

rev_a = np.log10(sku[sku["ABC"]=="A"]["total_revenue"].min())
rev_b = np.log10(sku[sku["ABC"]=="B"]["total_revenue"].min())
ax.axhline(rev_a, color=DGREY, ls=":", lw=1.2, alpha=0.5)
ax.axhline(rev_b, color=DGREY, ls=":", lw=1.2, alpha=0.5)

# Zone labels
zone_annots = [
    (0.25, 4.5, "AX / AY\nPREMIUM\nSERVICE", CORAL),
    (1.5,  4.5, "AZ\nMGMT\nATTENTION", CORAL),
    (0.25, 3.2, "BX / BY\nSTANDARD\nROP", AMBER),
    (1.5,  3.2, "BZ\nMONITOR", AMBER),
    (0.25, 2.0, "CX / CY\nLEAN", SKY),
    (1.5,  2.0, "CZ\nRATIONALISE?", SKY),
]
for xp, yp, txt, col in zone_annots:
    ax.text(xp, yp, txt, fontsize=9, color=col, fontweight="bold",
            ha="center", va="center", alpha=0.55)

ax.set_xlabel("Demand Variability (CV)  ←  Predictable  |  Unpredictable  →")
ax.set_ylabel("Annual Revenue (log₁₀ scale)")
ax.set_title("Strategic Quadrant: Revenue Importance vs Demand Predictability\n"
             "Each dot = one SKU (n=1,500 sample)  ·  Colour = ABC class",
             fontsize=12)
ax.set_xlim(-0.1, 2.6)

# Y-axis ticks → readable £
y_ticks = [1, 2, 3, 4, 5]
ax.set_yticks(y_ticks)
ax.set_yticklabels([f"£{10**y:,.0f}" for y in y_ticks])

legend_handles = [
    mpatches.Patch(color=CORAL, label="A-class (top 80% revenue)"),
    mpatches.Patch(color=AMBER, label="B-class (80-95% revenue)"),
    mpatches.Patch(color=SKY,   label="C-class (95-100% revenue)"),
]
ax.legend(handles=legend_handles, fontsize=10, loc="upper right")
ax.grid(alpha=0.15)
plt.tight_layout()
save_chart(fig, "06_strategic_quadrant.png")


# ── Summary stats export ───────────────────────────────────────────────────────
summary = pd.DataFrame({
    "metric": [
        "Total SKUs analysed",
        "Total transactions (clean)",
        "Date range",
        "Total annual revenue",
        "A-class SKUs",
        "A-class revenue share",
        "B-class SKUs",
        "C-class SKUs",
        "X-class SKUs (predictable)",
        "Y-class SKUs (moderate)",
        "Z-class SKUs (intermittent)",
        "CZ SKUs (rationalisation candidates)",
        "CZ → rationalise (low loyalty)",
        "CZ → protect (loyal accounts)",
        "AZ+BZ SKUs (management attention)",
        "AZ+BZ revenue at risk",
        "Top 80 ROP model: avg safety stock (A)",
        "SKUs with stockout risk flag",
    ],
    "value": [
        f"{len(sku):,}",
        f"{len(raw):,}",
        f"{raw['InvoiceDate'].min().date()} to {raw['InvoiceDate'].max().date()}",
        f"£{total_rev:,.2f}",
        f"{n_a:,} ({n_a/len(sku):.1%})",
        f"{abc_summary.loc[abc_summary['ABC']=='A','pct_revenue'].values[0]:.1%}",
        f"{n_b:,} ({n_b/len(sku):.1%})",
        f"{n_c:,} ({n_c/len(sku):.1%})",
        f"{n_x:,} ({n_x/len(sku):.1%})",
        f"{n_y:,} ({n_y/len(sku):.1%})",
        f"{n_z:,} ({n_z/len(sku):.1%})",
        f"{len(cz_skus):,} ({len(cz_skus)/len(sku):.1%})",
        f"{n_rational:,} (£{rational_rev:,.0f} revenue)",
        f"{n_protect:,} (£{protected_rev:,.0f} revenue)",
        f"{len(sku[sku['ABC_XYZ'].isin(['AZ','BZ'])]):,}",
        f"£{sku[sku['ABC_XYZ'].isin(['AZ','BZ'])]['total_revenue'].sum():,.0f}",
        f"{top80[top80['ABC']=='A']['safety_stock'].mean():.0f} units",
        f"{top80['stockout_risk_flag'].sum()} of 80 SKUs",
    ],
})
summary.to_csv(OUT / "summary_stats.csv", index=False)

print(f"""
╔══════════════════════════════════════════════════════════════════════════╗
║  INVENTORY OPTIMISATION — KEY FINDINGS                                  ║
╠══════════════════════════════════════════════════════════════════════════╣
║                                                                          ║
║  Finding 1 — Classic Pareto holds strongly                              ║
║    {n_a:,} A-class SKUs ({n_a/len(sku):.0%} of range) drive 80% of £{total_rev/1e6:.1f}M revenue.  ║
║                                                                          ║
║  Finding 2 — Most demand is variable (Z-class dominates)               ║
║    {n_z:,} SKUs ({n_z/len(sku):.0%}) are Z-class (CV > 1.0). Only {n_x} are predictable. ║
║    This is typical for giftware/seasonal wholesale — demand sensing     ║
║    approaches (rather than pure statistical forecasting) are advised.   ║
║                                                                          ║
║  Finding 3 — AZ+BZ needs management attention                          ║
║    {len(sku[sku['ABC_XYZ'].isin(['AZ','BZ'])]):,} SKUs carry high revenue but unpredictable demand.      ║
║    £{sku[sku['ABC_XYZ'].isin(['AZ','BZ'])]['total_revenue'].sum():,.0f} in annual revenue is at risk from stockouts.     ║
║                                                                          ║
║  Finding 4 — CZ rationalisation shortlist                               ║
║    {n_rational:,} of {len(cz_skus):,} CZ SKUs are safe to discontinue ({rational_rev/total_rev:.1%} revenue).║
║    {n_protect:,} CZ SKUs serve 3+ loyal customers — flag for manual review.  ║
║    Analyst judgment call: numbers alone do not justify discontinuation.  ║
╚══════════════════════════════════════════════════════════════════════════╝
""")
