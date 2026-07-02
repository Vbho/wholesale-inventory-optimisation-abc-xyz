-- ============================================================
-- 01_create_tables.sql
-- Inventory Optimisation — UK Wholesale Distributor
-- Analyst: Vaishnavi Bhor
--
-- Creates the base tables for the inventory analysis pipeline.
-- Run this first. Compatible with SQLite and PostgreSQL.
-- ============================================================

-- Raw transaction table (from ERP export / CSV import)
CREATE TABLE IF NOT EXISTS transactions (
    invoice_no      TEXT        NOT NULL,
    stock_code      TEXT        NOT NULL,
    description     TEXT,
    quantity        INTEGER     NOT NULL,
    invoice_date    TIMESTAMP   NOT NULL,
    unit_price      NUMERIC(10,2) NOT NULL,
    customer_id     TEXT,
    country         TEXT,
    revenue         NUMERIC(12,2) GENERATED ALWAYS AS (quantity * unit_price) STORED
);

-- Index for performance
CREATE INDEX IF NOT EXISTS idx_transactions_stock_code
    ON transactions(stock_code);

CREATE INDEX IF NOT EXISTS idx_transactions_invoice_date
    ON transactions(invoice_date);

CREATE INDEX IF NOT EXISTS idx_transactions_customer_id
    ON transactions(customer_id);

-- SKU master table (populated by 02_abc_classification.sql)
CREATE TABLE IF NOT EXISTS sku_master (
    stock_code              TEXT        PRIMARY KEY,
    description             TEXT,
    total_qty_sold          BIGINT,
    total_revenue           NUMERIC(12,2),
    n_invoices              INTEGER,
    n_customers             INTEGER,
    avg_unit_price          NUMERIC(10,4),
    first_sale_date         DATE,
    last_sale_date          DATE,
    weekly_demand_mean      NUMERIC(10,2),
    weekly_demand_std       NUMERIC(10,2),
    cv                      NUMERIC(8,4),
    weeks_with_demand       INTEGER,
    revenue_cumsum          NUMERIC(14,2),
    revenue_cum_pct         NUMERIC(6,4),
    abc_class               CHAR(1),      -- A, B, or C
    xyz_class               CHAR(1),      -- X, Y, or Z
    abc_xyz                 CHAR(2),      -- AX, AY, ... CZ
    strategic_action        TEXT,
    rationalisation_flag    TEXT          -- NULL, PROTECT, RATIONALISE, SINGLE_EVENT
);

-- Reorder point table (populated by 04_reorder_points.sql)
CREATE TABLE IF NOT EXISTS reorder_points (
    stock_code              TEXT        PRIMARY KEY,
    description             TEXT,
    abc_class               CHAR(1),
    xyz_class               CHAR(1),
    service_level_pct       INTEGER,
    lead_time_weeks         NUMERIC(4,1),
    avg_weekly_demand       NUMERIC(10,2),
    demand_std_weekly       NUMERIC(10,2),
    avg_demand_lt           NUMERIC(10,0),
    safety_stock            NUMERIC(10,0),
    reorder_point           NUMERIC(10,0),
    stockout_risk_flag      BOOLEAN,
    annual_revenue          NUMERIC(12,2),
    avg_unit_price          NUMERIC(10,4)
);

-- Weekly demand summary (intermediate, used for XYZ classification)
CREATE TABLE IF NOT EXISTS weekly_demand (
    stock_code      TEXT        NOT NULL,
    year_week       TEXT        NOT NULL,   -- e.g. '2011-W12'
    qty_sold        INTEGER     NOT NULL,
    PRIMARY KEY (stock_code, year_week)
);
