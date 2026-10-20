-- Optional data layer: imported job postings, calibration outcomes and the owner's fact vault.
-- The scoring engine never depends on these: empty tables (or Supabase down) leave every score as before.
-- Server-only access via the service role key, like the other screenproof_* tables.

-- Real job postings (no candidate data). Feeds the role keyword lists and the LinkedIn search map.
create table if not exists public.screenproof_postings (
  content_hash text primary key,          -- sha256 of normalized title|company|text: one row per posting
  title text not null,
  title_norm text not null,               -- lowercased, punctuation stripped: what role matching searches
  company text not null default '',
  location text not null default '',
  market text not null default '',        -- country, e.g. 'Canada'
  posted_on date,
  description text not null default '',
  terms jsonb not null default '[]',      -- skills/tools the posting asks for, as read at import
  source text not null default 'import',  -- import file kind: chatgpt | markdown
  import_hash text,
  created_at timestamptz not null default now()
);
create index if not exists screenproof_postings_title_idx on public.screenproof_postings (title_norm);
create index if not exists screenproof_postings_posted_idx on public.screenproof_postings (posted_on desc);

-- (score, outcome) pairs for calibration. Scores come from the normal scorer; no resume or job text is kept.
create table if not exists public.screenproof_outcomes (
  content_hash text primary key,
  score int not null check (score between 0 and 100),
  outcome text not null check (outcome in ('applied', 'interview', 'offer', 'rejected', 'no_reply')),
  role text not null default '',
  company text not null default '',
  applied_on date,
  source text not null default 'import',
  import_hash text,
  created_at timestamptz not null default now()
);

-- Owner-only fact vault: verified facts, achievement figures and removed/corrected claims.
create table if not exists public.screenproof_vault (
  content_hash text primary key,
  kind text not null check (kind in ('fact', 'figure', 'removed', 'corrected', 'limit')),
  claim text not null,
  figure text not null default '',
  status text not null check (status in ('verified', 'unverified', 'removed')),
  applies_to text not null default '',    -- role scope, e.g. 'Social Media Manager'; '' = every role
  max_mentions int,                       -- kind 'limit': state at most this many times
  correction text not null default '',
  import_hash text,
  created_at timestamptz not null default now()
);

-- One row per imported file, so re-importing the same file is a no-op.
create table if not exists public.screenproof_imports (
  content_hash text primary key,
  filename text not null default '',
  kind text not null,
  counts jsonb not null default '{}',
  created_at timestamptz not null default now()
);

alter table public.screenproof_postings enable row level security;
alter table public.screenproof_outcomes enable row level security;
alter table public.screenproof_vault enable row level security;
alter table public.screenproof_imports enable row level security;
revoke all on public.screenproof_postings, public.screenproof_outcomes, public.screenproof_vault, public.screenproof_imports
  from anon, authenticated;
