-- Read-only Postgres role for the agent evaluation harness collaborator.
--
-- Purpose: a collaborator developing the eval harness (docs/design/19-agent-harness-eval-design.md)
-- needs to READ the agent-readable schemas to build canonical reference retrieval (Task 4e)
-- and the label validator (Task 4d). They must NEVER write.
--
-- Scope: SELECT only on the two schemas the agent itself is allowed to read, mirroring the
-- app-level allowlist in services/alert_service/src/alert_service/agent/db_guard.py
--   reference.*  (financial_metrics, bronze_consensus_report, bronze_market_ticker, bronze_stock_overview)
--   serving.*    (symbol_snapshot, symbol_intraday_5m, symbol_daily_ohlc, symbol_signal_timeline)
-- The `agent.*`, `bronze.*`, `silver.*`, `gold.*` schemas are intentionally NOT granted.
--
-- Schema-level grants + ALTER DEFAULT PRIVILEGES are used (not per-table) because some serving
-- tables (5m/daily/timeline) are created lazily by intraday pipelines and may not exist yet;
-- a per-table GRANT would fail on a missing table.
--
-- Apply: must run as SUPERUSER `postgres`, NOT `invest`. The `invest` owner lacks CREATEROLE,
-- so CREATE ROLE fails under it. Use the postgres-credentials/postgres-password secret.
-- See ENV-SETUP "read-only DB role" for the exact ssh + k3s kubectl exec wrapper.
--   psql -U postgres -d invest_view -v role_pw="'CHANGE_ME_STRONG'" -f eval_readonly_role.sql
--
-- The :role_pw psql variable MUST be provided; there is no embedded default password.
-- ALTER DEFAULT PRIVILEGES targets objects created by the `invest` owner (the pipeline owner).

\set ON_ERROR_STOP on

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'eval_readonly') THEN
    CREATE ROLE eval_readonly LOGIN;
  END IF;
END
$$;

ALTER ROLE eval_readonly WITH PASSWORD :role_pw;

-- Connect + schema visibility (no CREATE — usage only).
GRANT CONNECT ON DATABASE invest_view TO eval_readonly;
GRANT USAGE ON SCHEMA reference, serving TO eval_readonly;

-- Read existing tables.
GRANT SELECT ON ALL TABLES IN SCHEMA reference, serving TO eval_readonly;

-- Read tables created later (e.g. serving.symbol_daily_ohlc after intraday warmup).
-- Applies to objects created by the `invest` owner from now on.
ALTER DEFAULT PRIVILEGES FOR ROLE invest IN SCHEMA reference, serving
  GRANT SELECT ON TABLES TO eval_readonly;

-- Verify the role has no write membership and report granted tables.
SELECT 'granted tables:' AS info, count(*) AS n
FROM information_schema.role_table_grants
WHERE grantee = 'eval_readonly' AND privilege_type = 'SELECT';
