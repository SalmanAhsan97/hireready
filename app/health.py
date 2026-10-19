"""Owner health check: is every outside service the app depends on reachable and accepting its key?

Each probe uses a free, read-only call (no model generation, no scraping), runs in parallel with a short
timeout, and reports only status and usage numbers: keys and raw provider responses are never returned.
Results are cached for a minute so refreshing the panel can't hammer the providers.
"""
import asyncio
import importlib.util
import os
import time

import httpx

from . import ai, cache, linkedin_jobs

TIMEOUT = 12
_last: tuple[float, dict] | None = None


def _status(ok: bool | None, detail: str) -> dict:
    return {"status": "ok" if ok else "off" if ok is None else "error", "detail": detail}


async def _ai(http: httpx.AsyncClient) -> dict:
    p = ai.provider()
    if p is None:
        return _status(None, "No AI key set: the app is in demo mode")
    if p == "gemini":
        r = await http.get(f"{ai.GEMINI_API}/{ai.GEMINI_MODEL}", headers={"x-goog-api-key": os.environ["GEMINI_API_KEY"]})
        name = ai.GEMINI_MODEL
    else:
        r = await http.get("https://api.anthropic.com/v1/models", headers={
            "x-api-key": os.getenv("ANTHROPIC_API_KEY", ""), "anthropic-version": "2023-06-01"})
        name = ai.MODEL
    extra = f"; premium writing model: {ai.PREMIUM_MODEL}" if ai.PREMIUM_MODEL else ""
    return _status(r.status_code == 200, (f"{name} reachable" if r.status_code == 200 else f"{p} returned HTTP {r.status_code}") + extra)


async def _supabase(http: httpx.AsyncClient) -> dict:
    if not cache.supabase_enabled():
        return _status(None, "Not configured: caches and history use local disk")
    r = await cache._sb().get("/screenproof_cache", params={"select": "kind", "limit": "1"})
    return _status(r.status_code == 200, "Tables reachable" if r.status_code == 200 else f"HTTP {r.status_code}")


async def _brightdata(http: httpx.AsyncClient) -> dict:
    key = os.getenv("BRIGHTDATA_API_KEY")
    if not key:
        return _status(None, "No key: LinkedIn job links use the public page")
    r = await http.get("https://api.brightdata.com/datasets/list", headers={"Authorization": f"Bearer {key}"})
    used, cap = await linkedin_jobs.usage(), linkedin_jobs.monthly_cap()
    usage = f"LinkedIn discovery {'on' if linkedin_jobs.enabled() else 'off'}: {used:,} of {cap:,} records this month"
    if r.status_code in (401, 403):
        return _status(False, f"Key rejected (HTTP {r.status_code}). {usage}")
    return _status(r.status_code == 200, ("Key accepted. " if r.status_code == 200 else f"HTTP {r.status_code}. ") + usage)


async def _apify(http: httpx.AsyncClient) -> dict:
    token = os.getenv("APIFY_API_TOKEN")
    if not token:
        return _status(None, "No token: profile links need the PDF upload")
    r = await http.get("https://api.apify.com/v2/users/me/limits", headers={"Authorization": f"Bearer {token}"})
    if r.status_code != 200:
        return _status(False, f"Token rejected or Apify unavailable (HTTP {r.status_code})")
    data = r.json().get("data", {})
    used = (data.get("current") or {}).get("monthlyUsageUsd")
    limit = (data.get("limits") or {}).get("maxMonthlyUsageUsd")
    spend = f"${used:,.2f} of ${limit:,.2f} used this month" if isinstance(used, (int, float)) and isinstance(limit, (int, float)) else "usage not reported"
    near = isinstance(used, (int, float)) and isinstance(limit, (int, float)) and limit and used / limit >= 0.8
    return _status(not near, ("Token accepted, " if not near else "Close to the monthly limit: ") + spend)


def _jobspy() -> dict:
    ok = importlib.util.find_spec("jobspy") is not None
    return _status(ok, "Installed: job suggestions and Indeed search map available" if ok else "python-jobspy not installed")


async def check(force: bool = False) -> dict:
    global _last
    if _last and not force and time.time() - _last[0] < 60:
        return _last[1]
    async with httpx.AsyncClient(timeout=TIMEOUT) as http:
        names = ["AI model", "Supabase", "Bright Data", "Apify"]
        probes = [_ai(http), _supabase(http), _brightdata(http), _apify(http)]
        results = await asyncio.gather(*probes, return_exceptions=True)
    services = {n: (r if isinstance(r, dict) else _status(False, f"Couldn't reach it ({type(r).__name__})"))
                for n, r in zip(names, results)}
    services["JobSpy"] = _jobspy()
    report = {"checked_at": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()), "services": services,
              "healthy": all(s["status"] != "error" for s in services.values())}
    _last = (time.time(), report)
    return report
