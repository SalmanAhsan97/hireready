"""Recent postings that fit a resume, via JobSpy (python-jobspy).

Indeed only by default: from a datacenter IP it answered every probe in under a second, with full
descriptions, while LinkedIn returned 429 after a few calls (and JobSpy turns that into an empty result,
not an error). Searches are cached for a few hours so repeat visitors don't re-scrape.
"""
import asyncio
import hashlib
import logging
import math
import os

from pydantic import BaseModel

from . import cache

log = logging.getLogger("hireready.jobs")

SITES = [s.strip() for s in os.getenv("HIREREADY_JOB_SITES", "indeed").split(",") if s.strip()]
HOURS_OLD = int(os.getenv("HIREREADY_JOB_HOURS_OLD", "336"))  # two weeks
CACHE_S = 6 * 3600
TIMEOUT_S = 25
MAX_DESCRIPTION = 15000

# Indeed needs a country; these are the ones offered in the UI (JobSpy supports many more).
COUNTRIES = ["USA", "Canada", "UK", "Australia", "India", "Pakistan", "UAE", "Saudi Arabia", "Germany", "Singapore"]


class JobSearchError(Exception):
    pass


class Posting(BaseModel):
    title: str
    fit: list[str] = []  # the resume's skills this posting mentions (filled by rank_by_fit)
    company: str = ""
    location: str = ""
    date_posted: str = ""
    url: str
    site: str
    remote: bool = False
    description: str = ""


class Postings(BaseModel):
    jobs: list[Posting]


def _text(v) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return ""
    return str(v).strip()


def _scrape(role: str, location: str, country: str, limit: int) -> list[Posting]:
    from jobspy import scrape_jobs  # pulls in pandas; import only when the feature is used

    df = scrape_jobs(
        site_name=SITES,
        search_term=role,
        location=location or None,
        results_wanted=limit,
        hours_old=HOURS_OLD,
        country_indeed=country,
        linkedin_fetch_description=False,
        description_format="markdown",
        verbose=0,
    )
    jobs, seen = [], set()
    for row in df.to_dict("records"):
        url = _text(row.get("job_url"))
        title = _text(row.get("title"))
        if not url.startswith(("https://", "http://")) or not title or url in seen:
            continue
        seen.add(url)
        jobs.append(Posting(
            title=title,
            company=_text(row.get("company")),
            location=_text(row.get("location")),
            date_posted=_text(row.get("date_posted"))[:10],
            url=url,
            site=_text(row.get("site")),
            remote=_text(row.get("is_remote")).lower() == "true",
            description=_text(row.get("description"))[:MAX_DESCRIPTION],
        ))
    jobs.sort(key=lambda j: j.date_posted, reverse=True)
    return jobs[:limit]


async def search(role: str, location: str = "", country: str = "USA", limit: int = 10) -> list[Posting]:
    role, location = role.strip()[:100], location.strip()[:100]
    if not role:
        raise JobSearchError("Score your resume first so we know which role to search for.")
    if country not in COUNTRIES:
        raise JobSearchError("Pick a country from the list.")
    key = hashlib.sha256(f"{','.join(SITES)}|{role.lower()}|{location.lower()}|{country}|{limit}".encode()).hexdigest()[:32]
    cached = await cache.get("job-search", key, Postings, max_age_s=CACHE_S)
    if cached:
        return cached.jobs
    try:
        jobs = await asyncio.wait_for(asyncio.to_thread(_scrape, role, location, country, limit), TIMEOUT_S)
    except asyncio.TimeoutError:
        raise JobSearchError("The job search took too long. Try again in a minute.")
    except Exception as e:  # JobSpy raises assorted network/parse errors
        log.warning("Job search failed: %r", e)
        raise JobSearchError("Couldn't reach the job boards just now. Try again in a minute.")
    if jobs:  # never cache an empty result: it may be a silent block rather than no jobs
        await cache.put("job-search", key, Postings(jobs=jobs))
    return jobs


def rank_by_fit(postings: list, skills: list[str]) -> list:
    """Most relevant first: postings that ask for more of the skills the resume already shows. Stable for ties."""
    if not skills:
        return postings
    from .scoring import _norm, keyword_hits
    from .synonyms import variants
    ranked = []
    for p in postings:
        text = _norm(f"{p.title} {p.description}")
        fit = [k for k in skills if keyword_hits([k, *variants(k)], text)]
        ranked.append(p.model_copy(update={"fit": fit}))
    return sorted(ranked, key=lambda p: -len(p.fit))
