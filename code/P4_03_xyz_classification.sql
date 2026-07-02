-- ============================================================
-- 03_xyz_classification.sql
-- XYZ Classification by Demand Variability (Coefficient of Variation)
-- Analyst: Vaishnavi Bhor
--
-- CV = σ_weekly_demand / μ_weekly_demand
--   X  CV ≤ 0.5   → predictable, stable demand
--   Y  0.5 < CV ≤ 1.0 → moderate variability
--   Z  CV > 1.0  → highly variable / intermittent demand
--
-- Weekly demand is used (not daily) to reduce noise from day-of-week
-- effects and align with typical replenishment cycles.
-- ============================================================

-- Step 1: Aggregate demand by SKU × week
CREATE TABLE IF NOT EXISTS sku_weekly_demand AS
SELECT
    stock_code,
    TO_CHAR(invoice_date, 'IYYY-IW')   AS year_week,   -- ISO year-week
    SUM(quantity)                        AS qty_sold
FROM clean_transactions
GROUP BY stock_code, TO_CHAR(invoice_date, 'IYYY-IW')
;

-- Step 2: Compute weekly demand statistics per SKU
CREATE TABLE IF NOT EXISTS sku_demand_stats AS
SELECT
    stock_code,
    ROUND(AVG(qty_sold)::NUMERIC, 2)    AS weekly_demand_mean,
    ROUND(STDDEV(qty_sold)::NUMERIC, 2) AS weekly_demand_std,
    COUNT(*)                             AS weeks_with_demand,
    -- Coefficient of Variation (CV)
    CASE
        WHEN AVG(qty_sold) > 0
        THEN ROUND(STDDEV(qty_sold) / AVG(qty_sold), 4)
        ELSE NULL
    END AS cv
FROM sku_weekly_demand
GROUP BY stock_code
;

-- Step 3: XYZ classification
CREATE TABLE IF NOT EXISTS sku_xyz AS
SELECT
    s.*,
    d.weekly_demand_mean,
    d.weekly_demand_std,
    d.weeks_with_demand,
    d.cv,
    CASE
        WHEN d.cv IS NULL     THEN 'Z'    -- no CV computable → treat as intermittent
        WHEN d.cv <= 0.5      THEN 'X'
        WHEN d.cv <= 1.0      THEN 'Y'
        ELSE                       'Z'
    END AS xyz_class
FROM sku_abc s
LEFT JOIN sku_demand_stats d USING (stock_code)
;

-- ── Combined ABC-XYZ table ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS sku_abc_xyz AS
SELECT
    *,
    abc_class || xyz_class AS abc_xyz,
    CASE abc_class || xyz_class
        WHEN 'AX' THEN 'Premium service (≥98% SL). Tight ROP. Forecast-driven replenishment.'
        WHEN 'AY' THEN 'High service (≥95% SL). Safety stock buffer. Weekly review.'
        WHEN 'AZ' THEN 'MANAGEMENT ATTENTION. High value, unpredictable. Demand sensing required.'
        WHEN 'BX' THEN 'Standard replenishment. System-driven ROP. Monthly review.'
        WHEN 'BY' THEN 'Standard replenishment. Moderate safety stock. Monthly review.'
        WHEN 'BZ' THEN 'Monitor closely. Collaborative forecasting with key customers.'
        WHEN 'CX' THEN 'Lean inventory. Minimum order quantities. Quarterly review.'
        WHEN 'CY' THEN 'Lean inventory. Consider make-to-order or VMI. Quarterly review.'
        WHEN 'CZ' THEN 'RATIONALISATION CANDIDATE. Review against loyal account dependencies.'
        ELSE 'Unclassified'
    END AS strategic_action
FROM sku_xyz
;

-- ── Validation: ABC-XYZ matrix ───────────────────────────────────────────────
SELECT
    abc_class,
    xyz_class,
    COUNT(*)                                            AS sku_count,
    ROUND(SUM(total_revenue), 0)                        AS revenue,
    ROUND(SUM(total_revenue)*100.0/SUM(SUM(total_revenue)) OVER (), 2)
                                                        AS pct_revenue
FROM sku_abc_xyz
GROUP BY abc_class, xyz_class
ORDER BY abc_class, xyz_class
;

/*
Expected cross-tab (counts):
         X     Y     Z
  A     32   418   366
  B     14   385   565
  C    392   805   811
*/
