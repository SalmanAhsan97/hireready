# HireReady

[![tests](https://github.com/SalmanAhsan97/hireready/actions/workflows/tests.yml/badge.svg)](https://github.com/SalmanAhsan97/hireready/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![Python 3.12](https://img.shields.io/badge/python-3.12-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)
[![Live demo](https://img.shields.io/badge/demo-live-4f46e5)](https://web-production-7af8e.up.railway.app)

![HireReady — resume scores you can actually trust](docs/social-preview.png)

**Score your resume against a real job, see exactly which requirements you meet, and fix the gaps — with scores that don't change every time you click.**

**Live demo:** [web-production-7af8e.up.railway.app](https://web-production-7af8e.up.railway.app) (free preview, no sign-up)

HireReady reads a resume (PDF, DOCX or text), optionally pulls a LinkedIn job posting, and returns a 0–100 match score, a requirement-by-requirement check, ranked edits, an AI resume editor and a ready-to-send application kit. It can also review a LinkedIn profile against the title you're targeting.

## Screenshots

| Job match score | Requirement-by-requirement check |
|---|---|
| ![Job match score and breakdown](docs/screenshots/03-job-match.png) | ![Must-have and nice-to-have coverage with quoted evidence](docs/screenshots/04-requirements.png) |
| **AI resume editor** | **LinkedIn profile review** |
| ![Resume chat with live preview](docs/screenshots/06-resume-chat.png) | ![LinkedIn profile scored against a target title](docs/screenshots/07-linkedin-review.png) |

<details>
<summary>More: home, loader, ranked edits</summary>

![Home](docs/screenshots/01-home.png)
![Staged loader with real upload progress](docs/screenshots/02-loader.png)
![Ranked, ready-to-paste edits](docs/screenshots/05-edits.png)

</details>

## What it does

| Flow | Input | Output |
|---|---|---|
| **Job match** | Resume + LinkedIn job link (or pasted description) | Match score, must-have / nice-to-have coverage with quoted evidence, missing keywords, ranked edits |
| **Resume score** | Resume only | General quality score for its best-fit role: keywords, impact, action verbs, structure, contact |
| **LinkedIn review** | Profile link or LinkedIn "Save to PDF" export + target title | Headline, About, keyword coverage, experience descriptions and titles, career timeline, skills, network, credibility, completeness, plus rewrites |
| **Resume chat** | Plain-language requests ("make my summary punchier") | Edited resume with live preview and .docx export |
| **Application kit** | Analysis + job | Tailored resume, cover letter, LinkedIn note, recruiter DM, hiring-manager email, interview prep |

## The interesting part: deterministic scoring

Most AI resume tools ask a model for a score, so the same resume gets a different number on every run. HireReady splits the work:

1. **The model extracts facts, once.** It lists the job's requirements (one skill per item, must-have vs nice-to-have, years, education) and quotes the resume line that supports each one. Extractions are cached by content hash, so the same inputs reuse the same facts.
2. **Code computes the score.** Python verifies every quote actually appears in the resume, matches keywords literally (with aliases and word-proximity, like an ATS), compares degree levels and years, and applies fixed weights. Same facts → same score, every time.
3. **The model explains it.** It writes rationales and suggested edits *after* the score is fixed, and is told not to re-score.

Weights follow [Jobscan's published priorities](https://www.jobscan.co/jobscan-tutorial) — hard skills first, then education, job title and other keywords — and categories the posting doesn't ask for are left out instead of awarding free points. Missing must-haves cap the score so a candidate can't look like a "top candidate" while lacking a stated requirement.

Abbreviations count for the skill ("K8s" shows Kubernetes, via a curated synonym map adapted from [ats-screener](https://github.com/sunnypatell/ats-screener), MIT), but the *ATS keyword coverage* stays word-for-word and the report tells you to add the posting's exact term, since strict ATS like Taleo search literally.

Hidden text can't buy points: white, invisible or microscopic text in a PDF is removed before scoring and the user is warned. (This trick inflated scores in [hiring-agent](https://github.com/interviewstreet/hiring-agent)'s LLM-based scorer.) White text on a dark designed panel and a scanned page's OCR layer are left alone.

Guardrails against made-up content: resume edits may only use facts from the resume or the user's own messages, and code flags any figure in an edit that appears in neither.

## Architecture

```
static/ (vanilla JS)  ──►  FastAPI  ──►  parsing.py   PDF/DOCX text (reflows wrapped bullets, recovers hyperlinks)
                                    │                Job postings: Bright Data + Apify in parallel, merged; public page fallback
                                    │                LinkedIn profiles: Apify, or the PDF export
                                    ├──►  ai.py      Claude (Anthropic SDK, structured outputs) or Gemini (JSON schema), with retries/fallback
                                    ├──►  scoring.py Deterministic scoring for job match, resume-only and LinkedIn profiles
                                    └──►  cache.py   Supabase (Postgres via PostgREST) for caches, history and rate limits; disk fallback
```

- **Backend:** Python 3.12, FastAPI, Pydantic, httpx, pypdf, python-docx
- **AI:** Anthropic Claude (default when a key is set) or Google Gemini
- **Data:** Bright Data and Apify scrapers for LinkedIn jobs and profiles
- **Storage:** Supabase Postgres, row-level security on, server-only access
- **Frontend:** vanilla HTML/CSS/JS — animated splash, staged loader with real upload progress, light/dark themes
- **Deploy:** Docker on Railway, auto-deploy from `main`

## Access model

Without an access code, visitors get a **free preview**: the score, category bars and a taste of the report. Locked sections are removed server-side (not just hidden in the browser) and link to the owner for pricing. With the code, the full report, resume chat, application kit and .docx export unlock. Previews never spend paid scraper credit, and every visitor has daily limits.

## Run it locally

```bash
git clone https://github.com/SalmanAhsan97/hireready.git
cd hireready
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env   # add an AI key; everything else is optional
.venv/bin/uvicorn app.main:app --port 8765 --reload
```

Open http://localhost:8765. With no AI key set, the app runs in demo mode with sample results so the whole flow can be clicked through.

Database tables are in [`supabase/migrations/`](supabase/migrations).

## Tests

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
```

The suite runs offline — no API keys, no database — and covers what the product promises: identical scores for identical inputs, quotes checked against the resume, degree comparison, ATS-style keyword matching, PDF line reflow, server-side redaction of the free preview, and access control. It runs on every push via GitHub Actions.

## License

[MIT](LICENSE)

## Author

Built by **Salman Ahsan** — marketing and growth professional with an MS in Business Analytics.
[LinkedIn](https://www.linkedin.com/in/salmanahsan9/) · [Portfolio](https://salmansworkportfolio.my.canva.site/salman-ahsan/)
