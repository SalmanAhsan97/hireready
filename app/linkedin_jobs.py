"""Recent LinkedIn postings for a role, via Bright Data's "discover by keyword" on the LinkedIn jobs dataset.

Billed per record, so: only full-access (Pro/admin) requests may spend records, each search asks for at most
LIMIT records, results are cached per role+country for a week, and a monthly counter stops calls at MONTHLY_CAP.
Discovery is asynchronous on Bright Data's side. If a snapshot isn't ready within WAIT_S, its id is kept and the
next request for the same role+country collects it, so records already paid for are never thrown away.
The API key stays in the request header: it is never logged or returned.
"""
import asyncio
import hashlib
import logging
import os
from datetime import datetime, timezone

import httpx
from pydantic import BaseModel

from . import cache
from .jobs import MAX_DESCRIPTION, Posting, Postings
from .settings import env

log = logging.getLogger("screenproof.linkedin_jobs")

BD_API = "https://api.brightdata.com/datasets/v3"
DATASET = os.getenv("BRIGHTDATA_LINKEDIN_JOBS_DATASET", "gd_lpfll7v5hcqtkxl6l")
LIMIT = 15
CACHE_S = 7 * 86400
WAIT_S = 60
POLL_S = 5


class Usage(BaseModel):
    records: int = 0


class Pending(BaseModel):
    snapshot_id: str
    started: str


def enabled() -> bool:
    default = "1" if os.getenv("BRIGHTDATA_API_KEY") else "0"
    return env("BD_DISCOVERY", default) == "1" and bool(os.getenv("BRIGHTDATA_API_KEY"))


def monthly_cap() -> int:
    return int(env("BD_MONTHLY_CAP", "4500"))


def _month() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


async def usage(month: str | None = None) -> int:
    u = await cache.get("bd-usage", month or _month(), Usage)
    return u.records if u else 0


async def _add_usage(n: int):
    if n:
        month = _month()
        await cache.put("bd-usage", month, Usage(records=await usage(month) + n))


def _key(role: str, country: str) -> str:
    return hashlib.sha256(f"{DATASET}|{' '.join(role.lower().split())}|{country}".encode()).hexdigest()[:32]


def _text(rec: dict, *keys) -> str:
    for k in keys:
        v = rec.get(k)
        if isinstance(v, dict):
            v = v.get("name") or v.get("text")
        if v:
            return str(v).strip()
    return ""


def to_postings(records) -> list[Posting]:
    from .parsing import _html_to_text
    out, seen = [], set()
    for rec in records if isinstance(records, list) else []:
        if not isinstance(rec, dict) or rec.get("error"):
            continue
        url, title = _text(rec, "url"), _text(rec, "job_title", "title")
        desc = _html_to_text(_text(rec, "job_description_formatted", "job_summary", "job_description", "description"))
        if not url.startswith("http") or not title or url in seen:
            continue
        seen.add(url)
        out.append(Posting(title=title, company=_text(rec, "company_name", "company"), location=_text(rec, "job_location", "location"),
                           date_posted=_text(rec, "job_posted_date", "job_posted_time")[:10], url=url, site="linkedin",
                           description=desc[:MAX_DESCRIPTION]))
    return out


async def _collect(http: httpx.AsyncClient, snapshot_id: str, wait_s: int) -> list | None:
    """The snapshot's records once ready; None if still running after wait_s; [] if it failed."""
    for i in range(max(1, wait_s // POLL_S)):
        if i:
            await asyncio.sleep(POLL_S)
        prog = (await http.get(f"{BD_API}/progress/{snapshot_id}")).json()
        status = prog.get("status")
        if status == "ready":
            data = (await http.get(f"{BD_API}/snapshot/{snapshot_id}", params={"format": "json"})).json()
            return data if isinstance(data, list) else []
        if status == "failed":
            return []
    return None


async def search(role: str, country: str, allowed: bool) -> list[Posting]:
    """LinkedIn postings for role+country, or [] when not allowed, disabled, over the cap, pending or failing.
    Never raises: the search map just uses Indeed alone."""
    if not enabled():
        return []
    key = _key(role, country)
    cached = await cache.get("bd-job-search", key, Postings, max_age_s=CACHE_S)
    if cached:
        return cached.jobs
    if not allowed:
        return []
    headers = {"Authorization": f"Bearer {os.environ['BRIGHTDATA_API_KEY']}"}
    try:
        async with httpx.AsyncClient(headers=headers, timeout=30) as http:
            pending = await cache.get("bd-pending", key, Pending, max_age_s=86400)
            if pending and pending.snapshot_id:
                snapshot_id = pending.snapshot_id
            else:
                if await usage() + LIMIT > monthly_cap():
                    log.info("Bright Data monthly cap reached; using Indeed only")
                    return []
                resp = await http.post(f"{BD_API}/trigger", params={
                    "dataset_id": DATASET, "type": "discover_new", "discover_by": "keyword",
                    "limit_per_input": str(LIMIT), "include_errors": "true",
                }, json=[{"keyword": role, "location": country, "time_range": "Past month"}])
                if resp.status_code != 200:
                    log.warning("Bright Data discovery trigger failed: HTTP %s", resp.status_code)
                    return []
                snapshot_id = resp.json()["snapshot_id"]
                await cache.put("bd-pending", key, Pending(snapshot_id=snapshot_id, started=datetime.now(timezone.utc).isoformat()))
            records = await _collect(http, snapshot_id, WAIT_S)
    except (httpx.HTTPError, ValueError, KeyError) as e:
        log.warning("Bright Data discovery failed: %s", type(e).__name__)
        return []
    if records is None:
        return []  # still running: the snapshot id is kept for the next request
    await cache.put("bd-pending", key, Pending(snapshot_id="", started=""))  # collected (an empty id reads as none)
    billed = sum(isinstance(r, dict) and not r.get("error") for r in records)  # Bright Data bills successful records
    await _add_usage(billed)
    jobs = to_postings(records)
    if billed:  # paid for, so cached even if none had a usable description
        await cache.put("bd-job-search", key, Postings(jobs=jobs))
    return jobs
