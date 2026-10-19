-- Screenproof (separate app sharing this project; tables keep the original hireready_ prefix). Server-only access via the service role key:
-- RLS is on with no policies, so anon/authenticated (publishable key) users can't read or write these.

create table if not exists public.hireready_cache (
  kind text not null,          -- 'postings' | 'jobs' | 'evidence'
  key text not null,
  value jsonb not null,
  updated_at timestamptz not null default now(),
  primary key (kind, key)
);

create table if not exists public.hireready_analyses (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  job_url text not null default '',
  job_title text not null default '',
  company text not null default '',
  candidate_name text not null default '',
  overall_score int not null,
  standing text not null,
  must_pct int,
  resume_sha256 text not null,  -- fingerprint only; resume text is not stored
  demo boolean not null default false,
  job jsonb not null,
  analysis jsonb not null
);

create index if not exists hireready_analyses_created_at_idx on public.hireready_analyses (created_at desc);
create index if not exists hireready_analyses_job_url_idx on public.hireready_analyses (job_url);

alter table public.hireready_cache enable row level security;
alter table public.hireready_analyses enable row level security;
revoke all on public.hireready_cache, public.hireready_analyses from anon, authenticated;
