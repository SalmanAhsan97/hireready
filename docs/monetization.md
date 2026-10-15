# HireReady: plans and monetization

HireReady is a web app for job seekers. The free tier earns trust with an honest score, and the paid tier pays for the expensive parts: a premium model for writing, paid scrapers for complete data, and deeper features.

## Principles

1. **One scoring engine for everyone.** Free, Pro and admin use the same extraction model, the same deterministic scoring code and the same checks. A subscription never buys a higher number, and the same resume + job always gets the same score.
2. **The free tier gets the best scoring the stack can do.** That means evidence-checked requirements, ATS-realistic keyword matching with synonyms, hidden-text stripping, must-have caps, and leaving out fields a source can't see instead of scoring them as zero. No accuracy is held back to upsell.
3. **Pay for depth, not for the verdict.** Paid plans add the full report, better-written guidance, tools that save hours (kit, chat, comparison, exports) and fuller job data.
4. **Most of the money goes back into quality.** A small operator margin; the rest funds model, data and scoring work (see below).

## Tiers

| Feature | Free preview | Pro (subscription) | Admin (owner) |
|---|---|---|---|
| Job match, resume and LinkedIn scores with category bars | ✓ | ✓ | ✓ |
| File warnings (hidden text) | ✓ | ✓ | ✓ |
| Requirement check with quoted evidence | — | ✓ | ✓ |
| All edits, strengths, gaps, red flags, missing keywords | first of each | ✓ | ✓ |
| Writing by the premium model (`HIREREADY_PREMIUM_MODEL`) | — | ✓ | ✓ |
| Resume chat and application kit | — | ✓ | ✓ |
| .docx and PDF export | — | ✓ | ✓ |
| **Resume comparison** for one job (2–4 resumes) | — | ✓ | ✓ |
| Job suggestions for a resume (Indeed) | — | ✓ | ✓ |
| Job links via Bright Data + Apify | public page | ✓ | ✓ |
| Saved resumes & jobs (cloud history, Preview, last used) | — | — | ✓ |
| Daily limits (defaults) | 3 previews | 15 runs, 40 chat, 10 job searches | none |

Limits are environment variables (`HIREREADY_PREVIEW_LIMIT`, `HIREREADY_DAILY_LIMIT`, `HIREREADY_CHAT_LIMIT`, `HIREREADY_JOBS_LIMIT`), so plans can be tuned without code changes.

## Resume comparison (Pro)

Upload 2–4 versions of a resume (or candidates' resumes) and one job link or description. HireReady:

- extracts the job's requirements **once** and checks every resume against that same list, so the scores compare fairly;
- scores each resume with the normal deterministic engine;
- ranks them by score, then must-haves met, then word-for-word keyword coverage, and states the best fit (or a tie) in a sentence computed in code;
- shows shortlist chances per resume (strong / good / fair / low, from the same standing bands as a single analysis), must-haves met, keyword match, what only that resume shows, and missing keywords;
- lays out a requirement-by-requirement grid (✓ met, ½ partly, ✗ missing).

It makes no extra AI call for the verdict. Each resume counts as one run toward the daily limit.

## What each feature costs to run

| Cost driver | When it's spent |
|---|---|
| Standard model (extraction) | Every new resume + job pair; cached by content hash, so repeats are free |
| Premium model (writing) | Paid narratives, rewrites, chat, application kit |
| Bright Data + Apify | Paid job links and LinkedIn profile links (postings cached for a while) |
| Supabase + Railway | Storage, history, usage limits and hosting |

## Where the subscription goes

- **Running the paid output:** premium model calls, scraper credits, hosting and storage.
- **Refining the scores:** a labelled set of resume/job pairs to calibrate weights against real screening outcomes, more synonym and skill coverage, and better parsing for unusual resume layouts.
- **Operator margin:** a small share for maintenance and support.

## Access today and rollout

- **Today:** Pro is unlocked with an access code issued on request (`HIREREADY_ACCESS_CODE`). The owner signs in from the header (`HIREREADY_ADMIN_USER` / `HIREREADY_ADMIN_PASSWORD`). Locked sections are removed server-side, not just hidden.
- **Next:** self-serve subscriptions (checkout plus per-user accounts) replace the shared access code, so each subscriber gets their own history and limits.
- **Later:** team plans for career coaches and recruiters who compare many resumes for one role.

## Privacy

Visitors' resume text is never stored; only a fingerprint (SHA-256) is kept with each analysis. Resume text is saved only for the owner's admin runs, so they can be reopened from Saved resumes & jobs. Supabase tables have row-level security on and are reachable only with the server's service key.
