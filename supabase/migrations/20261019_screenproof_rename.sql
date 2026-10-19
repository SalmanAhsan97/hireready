-- Rename the app's tables from the pre-rename hireready_ prefix to screenproof_.
-- Data, primary keys, row-level security and revoked grants all carry over with a rename.
alter table public.hireready_cache rename to screenproof_cache;
alter table public.hireready_analyses rename to screenproof_analyses;
alter table public.hireready_usage rename to screenproof_usage;

alter index public.hireready_cache_pkey rename to screenproof_cache_pkey;
alter index public.hireready_analyses_pkey rename to screenproof_analyses_pkey;
alter index public.hireready_analyses_created_at_idx rename to screenproof_analyses_created_at_idx;
alter index public.hireready_analyses_job_url_idx rename to screenproof_analyses_job_url_idx;
alter index public.hireready_usage_pkey rename to screenproof_usage_pkey;
alter index public.hireready_usage_client_created_idx rename to screenproof_usage_client_created_idx;
