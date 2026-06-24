-- Tznobar catalog schema for Supabase (Postgres)
-- Run this once in the Supabase SQL Editor.

create table if not exists products (
    id          bigint primary key,
    name        text not null,
    category    text,
    image       text,
    page        integer,
    description text,
    updated_at  timestamptz not null default now()
);

-- Keep the catalog ordered by id for stable display.
create index if not exists products_id_idx on products (id);

-- Enable Row Level Security and DO NOT add public policies.
-- The anon/public key gets ZERO access. All reads/writes go through
-- server.py using the service_role key, which bypasses RLS. This keeps
-- the catalog safe from anyone who finds the public key.
alter table products enable row level security;
