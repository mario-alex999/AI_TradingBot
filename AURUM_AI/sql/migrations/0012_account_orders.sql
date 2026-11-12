-- =====================================================================
-- AURUM AI - pending orders on the mirrored MT5 account, plus cleanup of duplicated mirror rows.
-- 1. account_orders: the limit / stop orders currently on each account (read with orders_get; never placed or
--    changed by AURUM). Replaced as a whole on every sync.
-- 2. The shared-account quote stream stored deals without the account number (login 0) as well as under the real
--    login, so history showed every trade twice. Rows under login 0 that also exist under a real login are removed;
--    login-0 rows with no counterpart are kept.
-- Safe to run more than once.
-- =====================================================================

CREATE TABLE IF NOT EXISTS account_orders (
    login         BIGINT       NOT NULL,
    ticket        BIGINT       NOT NULL,
    symbol        VARCHAR(64)  NOT NULL,
    broker_symbol VARCHAR(64)  NOT NULL,
    order_type    VARCHAR(20)  NOT NULL,
    side          VARCHAR(4)   NOT NULL,
    volume        DOUBLE PRECISION NOT NULL,
    price         DOUBLE PRECISION NOT NULL,
    stop_loss     DOUBLE PRECISION,
    take_profit   DOUBLE PRECISION,
    placed_at     TIMESTAMPTZ  NOT NULL,
    expires_at    TIMESTAMPTZ,
    magic         BIGINT,
    comment       TEXT,
    synced_at     TIMESTAMPTZ  NOT NULL DEFAULT now(),
    PRIMARY KEY (login, ticket)
);

DELETE FROM account_deals d
 WHERE d.login = 0
   AND EXISTS (SELECT 1 FROM account_deals x WHERE x.login <> 0 AND x.ticket = d.ticket);

DELETE FROM trade_records t
 WHERE t.login = 0
   AND EXISTS (SELECT 1 FROM trade_records x WHERE x.login <> 0 AND x.id = t.id);
