-- ============================================================
-- 04_reorder_points.sql
-- Reorder Point (ROP) Model — Top 80 Revenue SKUs
-- Analyst: Vaishnavi Bhor
--
-- Formula:
--   ROP = (μ_weekly × LT) + (z × σ_weekly × √LT)
--
-- Where:
--   μ_weekly  = average weekly demand
--   σ_weekly  = standard deviation of weekly demand
--   LT        = supplier lead time in weeks (by ABC class)
--   z         = standard normal z-score for target service level
--
-- Lead time assumptions (weeks):
--   A-class: 2 weeks (preferred suppliers, shorter contracts)
--   B-class: 3 weeks
--   C-class: 4 weeks
--
-- Service level targets (z-scores):
--   A-class: 98%  (z = 2.054)
--   B-class: 95%  (z = 1.645)
--   C-class: 90%  (z = 1.281)
--
-- Scope: top 80 SKUs by annual revenue, excluding single-event
--        bulk orders (weeks_with_demand = 1).
-- ============================================================

-- Step 1: Assign lead times and z-scores by class
CREATE TABLE IF NOT EXISTS class_parameters AS
SELECT * FROM (VALUES
    ('A', 2.0, 2.054, 98),
    ('B', 3.0, 1.645, 95),
    ('C', 4.0, 1.281, 90)
) AS t(abc_class, lead_time_weeks, z_score, service_level_pct)
;

-- Step 2: Compute ROP for eligible top-80 SKUs
CREATE TABLE IF NOT EXISTS reorder_points AS
WITH top80 AS (
    SELECT
        az.*,
        p.lead_time_weeks,
        p.z_score,
        p.service_level_pct,
        ROW_NUMBER() OVER (ORDER BY total_revenue DESC) AS revenue_rank
    FROM sku_abc_xyz az
    JOIN class_parameters p USING (abc_class)
    WHERE
        weeks_with_demand >= 3      -- exclude single-event / sparse SKUs
        AND cv IS NOT NULL          -- must have computable variability
)
SELECT
    stock_code,
    description,
    abc_class,
    xyz_class,
    abc_xyz,
    service_level_pct,
    lead_time_weeks,
    weekly_demand_mean                                          AS avg_weekly_demand,
    weekly_demand_std                                           AS demand_std_weekly,
    -- Average demand during lead time
    ROUND((weekly_demand_mean * lead_time_weeks)::NUMERIC, 0)   AS avg_demand_lt,
    -- Safety stock = z × σ × √(LT)
    ROUND((z_score * weekly_demand_std * SQRT(lead_time_weeks))::NUMERIC, 0)
                                                                AS safety_stock,
    -- Reorder point
    ROUND(
        (weekly_demand_mean * lead_time_weeks)
        + (z_score * weekly_demand_std * SQRT(lead_time_weeks))
        , 0)                                                    AS reorder_point,
    -- Stockout risk flag: CV > 1.2 = highly variable demand, increased risk
    (cv > 1.2)                                                  AS stockout_risk_flag,
    total_revenue                                               AS annual_revenue,
    avg_unit_price
FROM top80
WHERE revenue_rank <= 80
ORDER BY total_revenue DESC
;

-- ── Validation ────────────────────────────────────────────────────────────────
-- Average ROP and safety stock by class
SELECT
    abc_class,
    COUNT(*)                                AS skus,
    ROUND(AVG(avg_weekly_demand), 0)        AS avg_weekly_demand,
    ROUND(AVG(safety_stock), 0)             AS avg_safety_stock,
    ROUND(AVG(reorder_point), 0)            AS avg_rop,
    SUM(CASE WHEN stockout_risk_flag THEN 1 ELSE 0 END) AS stockout_risk_skus
FROM reorder_points
GROUP BY abc_class
ORDER BY abc_class
;

-- SKUs with highest reorder points (most critical to have in stock)
SELECT
    stock_code,
    description,
    abc_xyz,
    avg_weekly_demand,
    safety_stock,
    reorder_point,
    stockout_risk_flag,
    annual_revenue
FROM reorder_points
ORDER BY reorder_point DESC
LIMIT 20
;
