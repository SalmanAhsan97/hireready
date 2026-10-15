-- Per-client usage log for HireReady's daily limit. Server-only (service role), like the other hireready_ tables.
create table if not exists public.hireready_usage (
  id bigint generated always as identity primary key,
  created_at timestamptz not null default now(),
  client text not null,   -- salted hash of the caller's IP, never the raw IP
  action text not null    -- 'analyze' | 'kit'
);
create index if not exists hireready_usage_client_created_idx on public.hireready_usage (client, created_at desc);
alter table public.hireready_usage enable row level security;
revoke all on public.hireready_usage from anon, authenticated;
