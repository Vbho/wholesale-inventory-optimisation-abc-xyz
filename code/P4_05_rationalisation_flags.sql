-- ============================================================
-- 05_rationalisation_flags.sql
-- CZ SKU Rationalisation Analysis
-- Analyst: Vaishnavi Bhor
--
-- The ABC-XYZ framework identifies CZ SKUs as rationalisation
-- candidates, but the framework alone is insufficient. The
-- analyst judgment layer is critical:
--
--   (1) Single-event SKUs: appeared in only one week — likely
--       a one-off bulk order. Do NOT auto-discontinue.
--       Requires manual commercial review.
--
--   (2) Loyal account SKUs: >3 invoices OR >2 customers.
--       These have a repeat purchase pattern. Discontinuing
--       them could damage a high-margin customer relationship
--       that happens to be expressed in a low-revenue SKU.
--
--   (3) Rationalise: ≤3 invoices AND ≤2 customers AND
--       appeared in multiple weeks. Safe to consider removing.
--       Even then: get commercial sign-off before acting.
--
-- This file documents the business logic. The numbers are a
-- starting point for conversation, not an execution mandate.
-- ============================================================

-- CZ SKU rationalisation flags
CREATE TABLE IF NOT EXISTS cz_rationalisation AS
SELECT
    az.stock_code,
    az.description,
    az.total_revenue,
    az.n_invoices,
    az.n_customers,
    az.weekly_demand_mean,
    az.cv,
    az.weeks_with_demand,
    -- Flag 1: Single-event bulk order
    CASE WHEN az.weeks_with_demand = 1
         THEN TRUE ELSE FALSE END                    AS single_event_flag,
    -- Flag 2: Loyal account dependency
    CASE WHEN az.n_invoices > 3 OR az.n_customers > 2
         THEN TRUE ELSE FALSE END                    AS loyal_customer_flag,
    -- Action
    CASE
        WHEN az.weeks_with_demand = 1
            THEN 'SINGLE EVENT — one-off bulk order; manual commercial review required'
        WHEN az.n_invoices > 3 OR az.n_customers > 2
            THEN 'PROTECT — repeat purchase pattern detected; review before discontinue'
        ELSE
            'RATIONALISE CANDIDATE — low revenue, erratic demand, no repeat purchase pattern'
    END AS recommended_action
FROM sku_abc_xyz az
WHERE abc_xyz = 'CZ'
ORDER BY recommended_action, total_revenue DESC
;

-- ── Summary ───────────────────────────────────────────────────────────────────
SELECT
    recommended_action,
    COUNT(*)                            AS sku_count,
    ROUND(SUM(total_revenue), 0)        AS total_revenue,
    ROUND(AVG(total_revenue), 0)        AS avg_revenue_per_sku,
    ROUND(AVG(cv), 3)                   AS avg_cv,
    ROUND(AVG(n_customers), 1)          AS avg_customers
FROM cz_rationalisation
GROUP BY recommended_action
ORDER BY sku_count DESC
;

-- ── Analyst note ──────────────────────────────────────────────────────────────
/*
Key findings from this data:

1. True rationalisation candidates are FEW (19 SKUs, £886 total revenue).
   This is expected — in a wholesale business, most SKUs exist because
   a customer asked for them. Even CZ SKUs often have legitimate demand.

2. 136 "single event" SKUs need commercial review, not auto-discontinuation.
   One-off bulk orders can represent a new customer trial or seasonal spike.
   The right question is: "Is this customer likely to reorder?" — not
   "the numbers say Z, so remove it."

3. 792 CZ SKUs have repeat purchase patterns (PROTECT).
   These serve customers who order infrequently but consistently.
   In giftware/wholesale, this is not unusual — customers may order
   once a quarter for specific seasonal needs.

4. The ABC-XYZ framework is a structured starting point.
   It eliminates the excuse for acting without evidence.
   It does NOT eliminate the need for commercial judgment.

The recommendation: present these 19 rationalisation candidates to the
commercial team, alongside the 136 single-event items. Let the commercial
team confirm or override. Track any discontinued SKUs for 6 months to
measure actual revenue impact before making further cuts.
*/
