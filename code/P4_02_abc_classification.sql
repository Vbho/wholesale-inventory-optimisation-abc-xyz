-- ============================================================
-- 02_abc_classification.sql
-- ABC Classification by Revenue Contribution (Pareto)
-- Analyst: Vaishnavi Bhor
--
-- Step 1: Aggregate transactions to SKU level
-- Step 2: Compute cumulative revenue percentage (Pareto)
-- Step 3: Assign A / B / C class
--   A = top 80% of cumulative revenue
--   B = 80-95% of cumulative revenue
--   C = 95-100% of cumulative revenue
-- ============================================================

-- Step 1: Clean transactions (exclude cancellations, zero/negative qty & price)
CREATE TABLE IF NOT EXISTS clean_transactions AS
SELECT
    invoice_no,
    stock_code,
    description,
    quantity,
    invoice_date,
    unit_price,
    customer_id,
    country,
    (quantity * unit_price) AS revenue
FROM transactions
WHERE
    invoice_no NOT LIKE 'C%'           -- exclude cancellations
    AND quantity > 0
    AND unit_price > 0
    AND stock_code ~ '^[0-9A-Z]{5,6}$' -- well-formed stock codes only
;

-- Step 2: SKU-level revenue aggregation
CREATE TABLE IF NOT EXISTS sku_revenue AS
SELECT
    stock_code,
    -- Most common description (mode proxy using string aggregation)
    MAX(description) FILTER (WHERE description IS NOT NULL)    AS description,
    SUM(quantity)                                               AS total_qty_sold,
    ROUND(SUM(revenue)::NUMERIC, 2)                            AS total_revenue,
    COUNT(DISTINCT invoice_no)                                  AS n_invoices,
    COUNT(DISTINCT customer_id)                                 AS n_customers,
    ROUND(AVG(unit_price)::NUMERIC, 4)                          AS avg_unit_price,
    MIN(invoice_date::DATE)                                     AS first_sale_date,
    MAX(invoice_date::DATE)                                     AS last_sale_date
FROM clean_transactions
GROUP BY stock_code
;

-- Step 3: Pareto ranking and cumulative revenue
CREATE TABLE IF NOT EXISTS sku_abc AS
SELECT
    stock_code,
    description,
    total_qty_sold,
    total_revenue,
    n_invoices,
    n_customers,
    avg_unit_price,
    first_sale_date,
    last_sale_date,
    -- Running cumulative revenue (ranked highest to lowest)
    SUM(total_revenue) OVER (ORDER BY total_revenue DESC ROWS UNBOUNDED PRECEDING)
        AS revenue_cumsum,
    ROUND(
        SUM(total_revenue) OVER (ORDER BY total_revenue DESC ROWS UNBOUNDED PRECEDING)
        / SUM(total_revenue) OVER ()
        , 4
    ) AS revenue_cum_pct,
    -- ABC classification
    CASE
        WHEN ROUND(
            SUM(total_revenue) OVER (ORDER BY total_revenue DESC ROWS UNBOUNDED PRECEDING)
            / SUM(total_revenue) OVER ()
            , 4) <= 0.80 THEN 'A'
        WHEN ROUND(
            SUM(total_revenue) OVER (ORDER BY total_revenue DESC ROWS UNBOUNDED PRECEDING)
            / SUM(total_revenue) OVER ()
            , 4) <= 0.95 THEN 'B'
        ELSE 'C'
    END AS abc_class
FROM sku_revenue
ORDER BY total_revenue DESC
;

-- ── Validation queries ────────────────────────────────────────────────────────

-- Check: ABC summary (should show ~80%/15%/5% revenue split)
SELECT
    abc_class,
    COUNT(*)                                            AS sku_count,
    ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER (), 1) AS pct_skus,
    ROUND(SUM(total_revenue), 0)                        AS total_revenue,
    ROUND(SUM(total_revenue) * 100.0 / SUM(SUM(total_revenue)) OVER (), 1)
                                                        AS pct_revenue
FROM sku_abc
GROUP BY abc_class
ORDER BY abc_class
;

/*
Expected output (approximate):
abc_class | sku_count | pct_skus | total_revenue  | pct_revenue
A         |       816 |     21.5 |    8,179,328   |       80.0
B         |       964 |     25.4 |    1,533,870   |       15.0
C         |      2008 |     53.0 |      511,309   |        5.0
*/
