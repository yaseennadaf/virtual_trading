-- Run this once in your Supabase project's SQL editor.
-- Design goal: never lose a trade, and be able to reconcile positions
-- that were open when the app/browser was closed.

create table if not exists orders (
    id            bigserial primary key,
    created_at    timestamptz not null default now(),
    mode          text not null check (mode in ('replay', 'live_paper')),  -- real fills never go through 'orders'; they're imported straight into 'trades'
    symbol        text not null default 'BTCUSDT',
    side          text not null check (side in ('long', 'short')),
    order_type    text not null check (order_type in ('limit', 'stop', 'market')),
    status        text not null default 'pending' check (status in ('pending', 'filled', 'cancelled')),
    trigger_price numeric not null,
    quantity      numeric not null,
    stop_price    numeric,
    target_price  numeric,
    reason        text,                 -- why you took this trade
    regime_tag    text,                 -- 'trending' | 'ranging' | null (auto-classified)
    filled_at     timestamptz,
    filled_price  numeric,
    linked_trade_id bigint
);

create table if not exists trades (
    id               bigserial primary key,
    order_id         bigint references orders(id),
    mode             text not null check (mode in ('replay', 'live_paper', 'live_real')),
    symbol           text not null default 'BTCUSDT',
    side             text not null check (side in ('long', 'short')),
    entry_time       timestamptz not null,
    entry_price      numeric not null,
    exit_time        timestamptz,
    exit_price       numeric,
    quantity         numeric not null,
    stop_price       numeric,          -- null for imported real fills: SharkExchange's execution feed has no planned stop
    target_price     numeric,
    external_ref     text,             -- SharkExchange execution id(s), for de-duplicating re-imports
    fees_usd         numeric default 0,
    pnl_usd          numeric,
    pnl_inr          numeric,
    mae               numeric,          -- in R multiples
    mfe               numeric,          -- in R multiples
    exit_reason      text,             -- 'target' | 'stop' | 'manual' | 'reconciled_offline'
    reason           text,             -- entry rationale, free text
    regime_tag       text,             -- trending / ranging at entry
    status           text not null default 'open' check (status in ('open', 'closed')),
    inr_per_usdt_at_entry numeric,
    screenshot_url   text              -- storage path/URL of chart snapshot at entry
);

create table if not exists journal_entries (
    id            bigserial primary key,
    trade_id      bigint references trades(id),
    created_at    timestamptz not null default now(),
    notes         text,
    strategy_rules text,
    accuracy_context text,             -- e.g. running win-rate at time of entry
    screenshot_url text
);

create table if not exists equity_snapshots (
    id           bigserial primary key,
    ts           timestamptz not null default now(),
    mode         text not null,
    equity_inr   numeric not null
);

-- Single-row-per-mode table used purely for offline reconciliation:
-- "what was the last time we know for sure the state was correct".
create table if not exists app_state (
    mode              text primary key,
    last_seen_time    timestamptz not null,
    open_trade_id     bigint references trades(id)
);

create index if not exists idx_trades_status on trades(status);
create index if not exists idx_orders_status on orders(status);
create unique index if not exists idx_trades_external_ref on trades(external_ref) where external_ref is not null;

-- ---------------------------------------------------------------------
-- MIGRATION for databases created before this file was updated:
-- alter table trades drop constraint if exists trades_mode_check;
-- alter table trades add constraint trades_mode_check check (mode in ('replay','live_paper','live_real'));
-- alter table trades alter column stop_price drop not null;
-- alter table trades add column if not exists external_ref text;
-- create unique index if not exists idx_trades_external_ref on trades(external_ref) where external_ref is not null;
-- ---------------------------------------------------------------------
