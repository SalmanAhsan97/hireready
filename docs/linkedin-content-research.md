# LinkedIn content intelligence: data access options (checked 2026-10-21)

Goal: profile URL → similar people → what they post, when, and what performs → content calendar.

## GitHub options

| Repo | Stars | How it gets data | Fit |
|---|---|---|---|
| stickerdaniel/linkedin-mcp-server | ~4.0k, active daily | Drives a real browser with **your own signed-in LinkedIn session** (Patchright). Tools: `get_person_profile` (incl. posts), `search_posts`, `get_feed`, `get_company_posts`. | Best for owner use, run locally |
| joeyism/linkedin_scraper | ~4.6k | Playwright plus a saved login `session.json`. Profiles and company posts (reactions, comments, dates). | Same model, more code to own |
| tomquirk/linkedin-api (Voyager) | — | No longer on GitHub (404). Small forks remain. | Avoid |

All popular repos work the same way: a logged-in LinkedIn account automated in a browser. That is the workaround. It works, but LinkedIn's User Agreement bans automated access, and accounts that do it can be restricted. The repos' own sponsors (Unipile, Cadenza) sell hosted versions with rate limits for exactly this reason.

## Recommendation

1. **Owner mode (now, ~free).** Run `linkedin-mcp-server` locally with Salman's own account at a human pace (tens of profiles a day, not hundreds). Save results as an import `.json` → Screenproof's existing importer → the same analysis. No Bright Data cost; the account risk stays with the owner and stays small.
2. **Public users: never hold their LinkedIn sessions on our server.** Use one of:
   - Bright Data public profile/posts data (already integrated; pay per record; no account risk). Needs a probe: exact post timestamps? cost per profile?
   - Later: a Chrome extension that reads pages the user is already viewing. AuthoredUp and Taplio work this way: no automation, no stored credentials.
3. **Official LinkedIn sign-in only for publishing** from the calendar.

## Analysis (same rule as the app)

Model labels each post (topic, format, hook, has-number). Code computes cadence, engagement by weekday/hour, topic share vs engagement ("worth talking about" = high engagement, low saturation), and gaps vs the user's own posts. Calendar = weekly slots: day/time, topic, format, value score, draft hook.

## Bright Data probe (2026-10-21): it works, so build on it first

Locally, call it through DNS-over-HTTPS (`curl --doh-url https://1.1.1.1/dns-query`) to get past the ISP block; no VPN needed.
Test subject: one public profile (williamhgates). Both jobs were ready in under 45 s. 11 records used.

- **Profile** `gd_l1viktl72bvl7bjuj0` (trigger with `[{"url": ...}]`): followers, position, location, experience, plus
  `people_also_viewed` (19 peers: name, link, location) and `similar_profiles` (19). This peer list comes at no extra cost.
- **Posts** `gd_lyy3tktm25m4avu764`, `type=discover_new&discover_by=profile_url&limit_per_input=N`:
  `date_posted` is an **exact UTC timestamp** (good enough for day/hour timing advice), plus `num_likes`, `num_comments`, `post_type`
  (post/repost), `post_text`, `hashtags`, `images`/`videos`/`document_page_count` (format), `user_followers`.
  No impressions (expected).
- Cost model: 1 profile + P peers' posts × N each → e.g. 15 peers × 10 posts + own 10 ≈ **170 records per analysis**.
  The API key can't read the balance (no billing permission); check the per-record price in the dashboard.
- Still to check: peers of a famous account are other famous accounts; test on a typical profile.
  Track usage under its own monthly cap (the existing counter only covers the jobs dataset).
