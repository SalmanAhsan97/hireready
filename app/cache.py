"""Storage for fetched postings, Claude extractions and analysis history.

Uses Supabase (tables hireready_cache / hireready_analyses) when SUPABASE_URL and
SUPABASE_SERVICE_ROLE_KEY are set, so cached facts survive redeploys and are shared between instances.
Otherwise, or if Supabase is unreachable, falls back to JSON files in .cache/.
"""
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
from pydantic import BaseModel

log = logging.getLogger("hireready.cache")

CACHE_DIR = Path(os.getenv("HIREREADY_CACHE", Path(__file__).resolve().parent.parent / ".cache"))

_http: httpx.AsyncClient | None = None


def supabase_enabled() -> bool:
    return bool(os.getenv("SUPABASE_URL") and os.getenv("SUPABASE_SERVICE_ROLE_KEY"))


def _sb() -> httpx.AsyncClient:
    global _http
    if _http is None:
        key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
        headers = {"apikey": key}
        if key.startswith("eyJ"):  # legacy JWT service_role key; new sb_secret_ keys go in apikey only
            headers["Authorization"] = f"Bearer {key}"
        _http = httpx.AsyncClient(base_url=os.environ["SUPABASE_URL"].rstrip("/") + "/rest/v1", headers=headers, timeout=10)
    return _http


# ---- Key/value cache ----

async def get(kind: str, key: str, schema: type[BaseModel], max_age_s: float | None = None):
    if supabase_enabled():
        try:
            resp = await _sb().get(
                "/hireready_cache",
                params={"kind": f"eq.{kind}", "key": f"eq.{key}", "select": "value,updated_at"},
            )
            resp.raise_for_status()
            rows = resp.json()
            if not rows:
                return None
            if max_age_s is not None:
                age = datetime.now(timezone.utc) - datetime.fromisoformat(rows[0]["updated_at"])
                if age.total_seconds() > max_age_s:
                    return None
            return schema.model_validate(rows[0]["value"])
        except (httpx.HTTPError, ValueError, KeyError) as e:
            log.warning("Supabase cache read failed, using disk: %r", e)
    return _disk_get(kind, key, schema, max_age_s)


async def put(kind: str, key: str, value: BaseModel):
    if supabase_enabled():
        try:
            resp = await _sb().post(
                "/hireready_cache",
                params={"on_conflict": "kind,key"},
                headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
                json={
                    "kind": kind,
                    "key": key,
                    "value": value.model_dump(mode="json"),
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
            )
            resp.raise_for_status()
            return
        except httpx.HTTPError as e:
            log.warning("Supabase cache write failed, using disk: %r", e)
    _disk_put(kind, key, value)


def _disk_get(kind: str, key: str, schema: type[BaseModel], max_age_s: float | None):
    path = CACHE_DIR / kind / f"{key}.json"
    try:
        if max_age_s is not None and time.time() - path.stat().st_mtime > max_age_s:
            return None
        return schema.model_validate_json(path.read_text())
    except (OSError, ValueError):
        return None


def _disk_put(kind: str, key: str, value: BaseModel):
    path = CACHE_DIR / kind / f"{key}.json"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value.model_dump_json(indent=1))
    except OSError:
        pass


# ---- Analysis history ----

async def save_analysis(row: dict):
    """Best effort: a failed history write never fails the user's request."""
    if not supabase_enabled():
        return
    try:
        resp = await _sb().post("/hireready_analyses", headers={"Prefer": "return=minimal"}, json=row)
        resp.raise_for_status()
    except httpx.HTTPError as e:
        log.warning("Saving analysis to Supabase failed: %r", e)


# ---- Usage (daily limit) ----

_local_usage: dict[str, list[float]] = {}


async def count_and_record_usage(client: str, action: str, window_s: float = 86400) -> int:
    """Record one use and return how many uses this client has made in the window, including this one."""
    if supabase_enabled():
        try:
            since = datetime.fromtimestamp(time.time() - window_s, timezone.utc).isoformat()
            (await _sb().post("/hireready_usage", headers={"Prefer": "return=minimal"}, json={"client": client, "action": action})).raise_for_status()
            resp = await _sb().get(
                "/hireready_usage",
                params={"client": f"eq.{client}", "created_at": f"gte.{since}", "select": "id"},
                headers={"Prefer": "count=exact", "Range": "0-0"},
            )
            resp.raise_for_status()
            return int(resp.headers.get("content-range", "*/0").split("/")[-1])
        except (httpx.HTTPError, ValueError) as e:
            log.warning("Supabase usage tracking failed, counting in memory: %r", e)
    now = time.time()
    hits = [t for t in _local_usage.get(client, []) if now - t < window_s] + [now]
    _local_usage[client] = hits
    return len(hits)
