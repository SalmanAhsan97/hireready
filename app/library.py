"""Optional job-posting library: real postings the owner imported (role, company, market, date, text; no
candidate data).

It only adds signal on top of the built-in sources, never instead of them: library postings join the live
ones (Indeed, LinkedIn via Bright Data) when the search map is counted, weighted by how recent they are,
and lift the role keyword list once at least LIBRARY_MIN postings for the role exist. With no postings,
an empty table or Supabase down, every function here returns nothing and callers keep their result unchanged.
"""
import logging
import re
import time
from datetime import date

from pydantic import BaseModel

from . import cache
from .discover import canon
from .models import RoleKeyword
from .scoring import SENIORITY_WORDS, _norm
from .settings import env
from .synonyms import variants

log = logging.getLogger("screenproof.library")

MIN_POSTINGS = max(1, int(env("LIBRARY_MIN", "5")))  # fewer than this for a role and the keyword list stays as built
HALF_LIFE_DAYS = max(1, int(env("LIBRARY_HALF_LIFE_DAYS", "180")))  # a posting this old counts half
MAX_AGE_DAYS = int(env("LIBRARY_MAX_AGE_DAYS", "730"))  # older postings are left out
UNDATED_WEIGHT = 0.5
LIFT_SHARE = 20  # a library term asked for by at least this share of postings may join the keyword list
MAX_SWAPS = 5  # at most this many estimated keywords are swapped for library terms
MEMO_S = 600

_memo: dict[tuple, tuple[float, list]] = {}


class LibraryPosting(BaseModel):
    """Shaped like jobs.Posting, so it merges with live postings."""
    title: str
    company: str = ""
    location: str = ""
    date_posted: str = ""
    url: str = ""
    site: str = "library"
    remote: bool = False
    description: str = ""
    terms: list[str] = []
    weight: float = 1.0


def role_words(role: str) -> set[str]:
    return {w for w in _norm(role).split() if len(w) > 1 and w not in SENIORITY_WORDS}


def matches(title: str, role: str) -> bool:
    """A posting is for the role if its title has every core word of the role ("Senior Marketing Manager,
    Growth" is a Marketing Manager posting; "Marketing Coordinator" isn't)."""
    want = role_words(role)
    return bool(want) and want <= set(_norm(title).split())


def recency_weight(posted_on: str | None, today: date | None = None) -> float:
    """1.0 for a posting from today, halving every HALF_LIFE_DAYS; 0 once older than MAX_AGE_DAYS."""
    if not posted_on:
        return UNDATED_WEIGHT
    try:
        age = ((today or date.today()) - date.fromisoformat(str(posted_on)[:10])).days
    except ValueError:
        return UNDATED_WEIGHT
    if age > MAX_AGE_DAYS:
        return 0.0
    return round(0.5 ** (max(age, 0) / HALF_LIFE_DAYS), 4)


async def postings_for(role: str, country: str = "", limit: int = 30) -> list[LibraryPosting]:
    """Library postings for this role (and market, when both are known), newest first. [] on any failure."""
    words = sorted(w for w in role_words(role) if re.fullmatch(r"[a-z0-9]+", w))
    if not words:
        return []
    key = (" ".join(words), country, limit)
    hit = _memo.get(key)
    if hit and time.time() - hit[0] < MEMO_S:
        return hit[1]
    try:
        flt = {"and": "(" + ",".join(f"title_norm.ilike.*{w}*" for w in words) + ")"}
        rows = await cache.data_rows("postings", flt, limit=500, order="posted_on.desc.nullslast")
        out = []
        for r in rows:
            market = (r.get("market") or "").strip()
            if not matches(r.get("title", ""), role) or (country and market and market.lower() != country.lower()):
                continue
            weight = recency_weight(r.get("posted_on"))
            if weight <= 0:
                continue
            out.append(LibraryPosting(
                title=r["title"], company=r.get("company") or "", location=r.get("location") or market,
                date_posted=str(r.get("posted_on") or ""), description=r.get("description") or "",
                terms=[t for t in (r.get("terms") or []) if isinstance(t, str)], weight=weight,
            ))
        out.sort(key=lambda p: p.date_posted, reverse=True)
        out = out[:limit]
    except Exception as e:  # the library is extra signal; it must never break a request
        log.warning("Library postings unavailable: %r", e)
        out = []
    _memo[key] = (time.time(), out)
    return out


def term_shares(postings: list[LibraryPosting]) -> list[tuple[str, int]]:
    """Each skill/tool the postings list, with the recency-weighted percent of postings that list it."""
    total = sum(p.weight for p in postings)
    if not total:
        return []
    weight: dict[str, float] = {}
    spell: dict[str, dict[str, int]] = {}
    for p in postings:
        for key in {canon(t): t for t in p.terms if canon(t)}:
            weight[key] = weight.get(key, 0) + p.weight
        for t in p.terms:
            k = canon(t)
            if k:
                counts = spell.setdefault(k, {})
                counts[t] = counts.get(t, 0) + 1
    out = [(max(spell[k], key=lambda s: (spell[k][s], s)), round(100 * w / total)) for k, w in weight.items()]
    out.sort(key=lambda x: (-x[1], x[0].lower()))
    return out


def lift_keywords(base: list[RoleKeyword], shares: list[tuple[str, int]]) -> list[RoleKeyword]:
    """The estimated keyword list with up to MAX_SWAPS of the terms no library posting asks for swapped
    (from the bottom) for terms at least LIFT_SHARE% of library postings ask for. No shares = base unchanged."""
    if not shares:
        return base
    asked = {canon(t) for t, _ in shares}
    have = {canon(k.term) for k in base}
    adds = [t for t, s in shares if s >= LIFT_SHARE and canon(t) not in have][:MAX_SWAPS]
    unasked = [i for i in range(len(base) - 1, -1, -1) if canon(base[i].term) not in asked][:len(adds)]
    out = list(base)
    for i, term in zip(unasked, adds):
        out[i] = RoleKeyword(term=term, aliases=variants(term))
    return out


async def role_keywords(role: str, base: list[RoleKeyword]) -> tuple[list[RoleKeyword], int]:
    """(keyword list, library postings it was lifted with). Below MIN_POSTINGS: (base, 0), unchanged."""
    found = [p for p in await postings_for(role, limit=200) if p.terms]
    if len(found) < MIN_POSTINGS:
        return base, 0
    lifted = lift_keywords(base, term_shares(found))
    return lifted, len(found)
