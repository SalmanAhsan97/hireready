"""Content plan: what people similar to the user post on LinkedIn, when it lands, and a 4-week calendar.

Data comes from Bright Data's public LinkedIn datasets: the profile record lists similar people ("people also
viewed"), and the posts dataset gives each person's recent posts with exact timestamps, likes and comments.
Billed per record, so only full-access requests may spend records, a monthly counter stops at CONTENT_MONTHLY_CAP,
and every person's posts are cached for a week and shared across users (the second user with the same peers
costs nothing). Collection is asynchronous: an unfinished snapshot is kept and the next request picks it up.

Same split as the rest of the app: the model only labels each post (topic, hook style) and writes first lines;
code computes cadence, engagement by day and hour, format and topic performance, value scores and the calendar.
"""
import asyncio
import hashlib
import json
import logging
import os
import re
import statistics
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from pydantic import BaseModel

from . import cache
from .discover import canon
from .linkedin_jobs import BD_API, Usage, _collect
from .models import (Benchmark, CalendarSlot, ContentPlan, ContentPost, FormatStat, PostLabel, PostLabels, TimeSlot,
                     TopicHooks, TopicIdea)
from .scoring import _norm, keyword_hits
from .settings import env
from .synonyms import variants

log = logging.getLogger("screenproof.content")

PROFILE_DATASET = os.getenv("BRIGHTDATA_LINKEDIN_PROFILE_DATASET", "gd_l1viktl72bvl7bjuj0")
POSTS_DATASET = os.getenv("BRIGHTDATA_LINKEDIN_POSTS_DATASET", "gd_lyy3tktm25m4avu764")
PEERS = int(env("CONTENT_PEERS", "15"))
ROLE_PEERS = int(env("CONTENT_ROLE_PEERS", "10"))  # of PEERS: people with the target title in the market, found by search
APIFY_API = "https://api.apify.com/v2"
GOOGLE_ACTOR = "apify~google-search-scraper"
COUNTRY_CODES = {"USA": "us", "Canada": "ca", "UK": "gb", "Australia": "au", "India": "in", "Pakistan": "pk", "UAE": "ae",
                 "Saudi Arabia": "sa", "Germany": "de", "Singapore": "sg"}
POSTS_PER_PERSON = int(env("CONTENT_POSTS_PER_PERSON", "10"))
PEERS_CACHE_S = 30 * 86400
POSTS_CACHE_S = 7 * 86400
WAIT_S = 40  # per request; the page asks again while a snapshot is still running
WINDOW_DAYS = 365  # posts older than this don't count (many people post a few times a year)
CADENCE_DAYS = 90
MIN_BUCKET = 3  # a day/time or format needs this many peer posts before it's ranked
MIN_TOPIC_AUTHORS = 2
LABEL_BUDGET_S = 90  # the plan never waits longer than this on the model to label posts
HOOK_BUDGET_S = 60
GENERIC_TAGS = {"linkedin", "hiring", "jobs", "job", "career", "careers", "motivation", "success", "life", "pakistan", "india",
                "love", "work", "opentowork", "growth", "business", "leadership", "inspiration", "news", "update"}
MIN_MEASURED = 20  # fewer peer posts and the times are general guidance, not measured
WEEKS = 4
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
# proves a skill (30) + uncrowded (20) + engagement unknown (10): below a measured topic that proves a skill and performs
OPEN_GROUND_VALUE = 60
DEFAULT_SLOTS = [(1, 8), (2, 12), (3, 8)]  # Tue 8-10, Wed 12-14, Thu 8-10: general guidance when unmeasured


def monthly_cap() -> int:
    return int(env("CONTENT_MONTHLY_CAP", "3000"))


def enabled() -> bool:
    return bool(os.getenv("BRIGHTDATA_API_KEY"))


class Peer(BaseModel):
    name: str = ""
    url: str
    source: str = "viewed"  # viewed = LinkedIn's "people also viewed"; role = same title in the same market, by search


class Peers(BaseModel):
    peers: list[Peer]


class AuthorPosts(BaseModel):
    posts: list[ContentPost]


class Pending(BaseModel):
    snapshot_id: str = ""
    urls: list[str] = []


def _month() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


async def usage(month: str | None = None) -> int:
    u = await cache.get("bd-content-usage", month or _month(), Usage)
    return u.records if u else 0


async def _add_usage(n: int):
    if n:
        month = _month()
        await cache.put("bd-content-usage", month, Usage(records=await usage(month) + n))


def profile_id(url: str) -> str | None:
    from .parsing import linkedin_profile_id
    return linkedin_profile_id((url or "").strip())


def _url(pid: str) -> str:
    return f"https://www.linkedin.com/in/{pid}/"


# ---- Reading Bright Data records (code, not the model) ----

def peers_from_profile(rec: dict, own: str, limit: int = PEERS) -> list[Peer]:
    out, seen = [], {own}
    for item in (rec.get("people_also_viewed") or []) + (rec.get("similar_profiles") or []):
        if not isinstance(item, dict):
            continue
        pid = profile_id(item.get("profile_link") or item.get("url") or "")
        if pid and pid not in seen:
            seen.add(pid)
            out.append(Peer(name=str(item.get("name") or "")[:80], url=_url(pid)))
    return out[:limit]


def _format(rec: dict) -> str:
    repost = rec.get("repost")  # present on every post; only a repost has its id or link filled in
    if (rec.get("post_type") or "").lower() == "repost" or (isinstance(repost, dict) and (repost.get("repost_id") or repost.get("repost_url"))):
        return "repost"
    if rec.get("videos") or rec.get("video_duration"):
        return "video"
    if rec.get("document_page_count"):
        return "document"
    if "/pulse/" in str(rec.get("url") or "") or (rec.get("post_type") or "").lower() == "article":
        return "article"
    if rec.get("images"):
        return "image"
    return "text"


def _int(v) -> int:
    try:
        return max(0, int(v or 0))
    except (TypeError, ValueError):
        return 0


def post_from_record(rec: dict, author: str, author_url: str) -> ContentPost | None:
    if not isinstance(rec, dict) or rec.get("error") or not rec.get("date_posted"):
        return None
    try:
        posted = datetime.fromisoformat(str(rec["date_posted"]).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None
    pid = str(rec.get("id") or hashlib.sha256(f"{author_url}|{rec['date_posted']}".encode()).hexdigest()[:16])
    return ContentPost(id=pid, author=author, author_url=author_url, posted_at=posted.isoformat(), likes=_int(rec.get("num_likes")),
                       comments=_int(rec.get("num_comments")), format=_format(rec),
                       text=" ".join(str(rec.get("post_text") or rec.get("title") or "").split())[:500], url=str(rec.get("url") or ""))


def _record_author(rec: dict) -> str | None:
    """The person whose activity this record came from. "discovery_input" holds the profile link we asked for;
    "input" holds the post's own link, so it's only a fallback."""
    for v in (rec.get("discovery_input"), rec.get("input")):
        if isinstance(v, dict) and (pid := profile_id(v.get("url") or "")):
            return pid
    return None


def _written_by(rec: dict, person: str) -> bool:
    """A person's activity also lists posts by others they reacted to or reposted (companies, creators)."""
    uid = str(rec.get("user_id") or "").lower()
    return not uid or uid == person


# ---- Collection ----

class Collecting(Exception):
    """A snapshot is still running; ask again shortly."""


class Unavailable(Exception):
    pass


def _http() -> httpx.AsyncClient:
    return httpx.AsyncClient(headers={"Authorization": f"Bearer {os.environ['BRIGHTDATA_API_KEY']}"}, timeout=30)


async def _run(kind: str, key: str, dataset: str, params: dict, inputs: list[dict], estimate: int) -> list:
    """Trigger (or resume) one snapshot and collect it within WAIT_S. Raises Collecting if it's still running."""
    pending = await cache.get("bd-content-pending", f"{kind}-{key}", Pending, max_age_s=86400)
    async with _http() as http:
        if pending and pending.snapshot_id:
            snapshot_id = pending.snapshot_id
        else:
            if await usage() + estimate > monthly_cap():
                raise Unavailable("The content plan has reached this month's data limit. It resets on the 1st.")
            resp = await http.post(f"{BD_API}/trigger", params={"dataset_id": dataset, "include_errors": "true", **params}, json=inputs)
            if resp.status_code != 200:
                log.warning("Bright Data %s trigger failed: HTTP %s", kind, resp.status_code)
                raise Unavailable("Couldn't start reading LinkedIn just now. Try again in a few minutes.")
            snapshot_id = resp.json()["snapshot_id"]
            await cache.put("bd-content-pending", f"{kind}-{key}", Pending(snapshot_id=snapshot_id, urls=[i["url"] for i in inputs]))
        records = await _collect(http, snapshot_id, WAIT_S)
    if records is None:
        raise Collecting()
    await cache.put("bd-content-pending", f"{kind}-{key}", Pending())
    await _add_usage(sum(isinstance(r, dict) and not r.get("error") for r in records))
    return records


async def has_started(pid: str) -> bool:
    """Whether this profile's plan is already cached or being collected (so asking again isn't a new plan)."""
    if await cache.get("li-peers", pid, Peers, max_age_s=PEERS_CACHE_S) is not None:
        return True
    pending = await cache.get("bd-content-pending", f"peers-{pid}", Pending, max_age_s=86400)
    return bool(pending and pending.snapshot_id)


def peers_from_search(items: list, role: str, exclude: set[str]) -> list[Peer]:
    """Google results for site:linkedin.com/in "<role>" <country> → people whose headline has the role's core words."""
    from .library import matches
    out, seen = [], set(exclude)
    for item in items if isinstance(items, list) else []:
        for o in (item.get("organicResults") or []) if isinstance(item, dict) else []:
            pid = profile_id(str(o.get("url") or ""))
            name, _, headline = str(o.get("title") or "").partition(" - ")
            if pid and pid not in seen and matches(headline or name, role):
                seen.add(pid)
                out.append(Peer(name=name.strip()[:80], url=_url(pid), source="role"))
    return out


async def role_peers(role: str, country: str, exclude: set[str], allowed: bool) -> list[Peer]:
    """People with the target title in the target market, via one Google search (Apify), cached 30 days per role and
    market and shared across users. [] when unavailable, so the plan falls back to "people also viewed" alone."""
    role = " ".join(role.split())[:80]
    if not role:
        return []
    key = hashlib.sha256(f"{role.lower()}|{country}".encode()).hexdigest()[:32]
    cached = await cache.get("li-role-peers", key, Peers, max_age_s=PEERS_CACHE_S)
    if cached is None:
        if not (allowed and os.getenv("APIFY_API_TOKEN")):
            return []
        query = f'site:linkedin.com/in "{role}"' + (f" {country}" if country else "")
        try:
            async with httpx.AsyncClient(timeout=120) as http:
                resp = await http.post(f"{APIFY_API}/acts/{GOOGLE_ACTOR}/run-sync-get-dataset-items",
                                       headers={"Authorization": f"Bearer {os.environ['APIFY_API_TOKEN']}"},
                                       json={"queries": query, "maxPagesPerQuery": 2, "resultsPerPage": 10, "saveHtml": False,
                                             "countryCode": COUNTRY_CODES.get(country, "")})
            items = resp.json() if resp.status_code in (200, 201) else None
        except (httpx.HTTPError, ValueError) as e:
            log.warning("Role peer search failed: %s", type(e).__name__)
            items = None
        if items is None:
            return []  # not cached: the next request tries again
        cached = Peers(peers=peers_from_search(items, role, set()))
        await cache.put("li-role-peers", key, cached)
    return [p for p in cached.peers if profile_id(p.url) not in exclude]


async def peers_for(pid: str, allowed: bool) -> list[Peer] | None:
    cached = await cache.get("li-peers", pid, Peers, max_age_s=PEERS_CACHE_S)
    if cached is not None:
        return cached.peers
    if not allowed:
        return None
    records = await _run("peers", pid, PROFILE_DATASET, {}, [{"url": _url(pid)}], 1)
    rec = next((r for r in records if isinstance(r, dict) and not r.get("error")), None)
    peers = peers_from_profile(rec, pid) if rec else []
    await cache.put("li-peers", pid, Peers(peers=peers))
    return peers


def group_records(records: list, people: list[str], names: dict[str, str]) -> dict[str, list[ContentPost]]:
    """Posts-dataset records → posts per person asked for (profile id), split into their own and ones they engaged with."""
    grouped: dict[str, list[ContentPost]] = {m: [] for m in people}
    for rec in records:
        author = _record_author(rec) if isinstance(rec, dict) else None
        if author not in grouped:
            continue
        if _written_by(rec, author):
            post = post_from_record(rec, names.get(author, ""), _url(author))
        else:  # a post this person engaged with: signal for topics and timing, not for their own cadence
            other = str(rec.get("use_url") or "").split("?")[0] or f"https://www.linkedin.com/in/{rec.get('user_id')}/"
            post = post_from_record(rec, str(rec.get("user_id") or ""), other)
            if post:
                post.engaged = True
        if post:
            grouped[author].append(post)
    return grouped


async def posts_for(pid: str, people: list[Peer], allowed: bool) -> dict[str, list[ContentPost]]:
    """Posts per person (profile id → posts), from the shared weekly cache plus one snapshot for the rest."""
    out: dict[str, list[ContentPost]] = {}
    names = {}
    missing = []
    for p in people:
        ppid = profile_id(p.url)
        if not ppid:
            continue
        names[ppid] = p.name
        cached = await cache.get("li-posts", ppid, AuthorPosts, max_age_s=POSTS_CACHE_S)
        if cached is not None:
            out[ppid] = cached.posts
        else:
            missing.append(ppid)
    if missing and allowed:
        key = hashlib.sha256(",".join(sorted(missing)).encode()).hexdigest()[:24]
        records = await _run("posts", f"{pid}-{key}", POSTS_DATASET,
                             {"type": "discover_new", "discover_by": "profile_url", "limit_per_input": str(POSTS_PER_PERSON)},
                             [{"url": _url(m)} for m in missing], len(missing) * POSTS_PER_PERSON)
        grouped = group_records(records, missing, names)
        for m, posts in grouped.items():  # cached even when empty: someone who doesn't post is an answer too
            await cache.put("li-posts", m, AuthorPosts(posts=posts))
            out[m] = posts
    return out


# ---- Labels and first lines (the model) ----

LABEL_INSTRUCTIONS = """Label each LinkedIn post above. For every post id give: topic, the subject in 1-4 plain words \
("design systems", "job search", "team hiring", "product launch", "career lesson"), reusing the same wording for the \
same subject across posts; and hook, how the first line opens (question, story, number, list, opinion, announcement, other). \
Label what the post is about, not who wrote it."""

HOOK_INSTRUCTIONS = """Write one first line for a LinkedIn post on each topic below, for someone targeting <role>. \
Plain, specific, under 18 words, no hashtags, no emojis, no clichés, no invented numbers or results; use a [placeholder] \
where a real figure or name should go. Where a topic proves a skill, make the line set up a concrete example of it."""


async def labels_for(posts: list[ContentPost]) -> dict[str, PostLabel]:
    from . import ai
    out: dict[str, PostLabel] = {}
    todo = []
    for p in posts:
        hit = await cache.get("post-labels", p.id, PostLabel)
        if hit:
            out[p.id] = hit
        elif p.text and p.format != "repost":
            todo.append(p)
    if ai.demo_mode():
        from .demo import demo_label
        for p in todo:
            out[p.id] = demo_label(p)
        return out

    async def label(part: list[ContentPost]):
        block = "\n\n".join(f'<post id="{p.id}">{p.text[:400]}</post>' for p in part)
        try:
            got = await ai._parse(f"{block}\n\n{LABEL_INSTRUCTIONS}", PostLabels, effort="low")
        except ai.AIError as e:
            log.warning("Labelling posts failed: %s", e)
            return
        ids = {p.id for p in part}
        for lab in got.labels:
            if lab.id in ids and lab.topic.strip():
                lab.topic = " ".join(lab.topic.lower().split())[:40]
                out[lab.id] = lab
                await cache.put("post-labels", lab.id, lab)

    tasks = [asyncio.create_task(label(todo[i:i + 40])) for i in range(0, len(todo), 40)]
    if tasks:
        done, pending = await asyncio.wait(tasks, timeout=LABEL_BUDGET_S)
        for t in pending:  # a slow model doesn't hold the plan hostage; the next run labels what's left
            t.cancel()
        if pending:
            log.warning("Labelling posts ran past %ss; %d batches use hashtags instead", LABEL_BUDGET_S, len(pending))
    for p in todo:  # unlabelled: the post's own first hashtag, read in code (not cached, so the model gets another go)
        if p.id not in out and (tag := hashtag_topic(p.text)):
            out[p.id] = PostLabel(id=p.id, topic=tag, hook="other")
    return out


def hashtag_topic(text: str) -> str:
    """#DigitalMarketing → "digital marketing"; skips generic tags."""
    for tag in re.findall(r"#(\w{3,40})", text or ""):
        words = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", tag).replace("_", " ").lower().strip()
        if tag.lower() not in GENERIC_TAGS and words not in GENERIC_TAGS:
            return words
    return ""


async def hooks_for(role: str, slots: list[CalendarSlot], topics: list[TopicIdea]) -> dict[str, str]:
    from . import ai
    wanted = list(dict.fromkeys(s.topic for s in slots))
    if not wanted:
        return {}
    if ai.demo_mode():
        from .demo import demo_hook
        return {t: demo_hook(t) for t in wanted}
    proves = {t.topic: t.proves for t in topics}
    key = hashlib.sha256(json.dumps([role.lower(), wanted]).encode()).hexdigest()[:32]
    cached = await cache.get("content-hooks", key, TopicHooks)
    if not cached:
        lines = "\n".join(f"- {t}" + (f" (proves: {', '.join(proves.get(t, []))})" if proves.get(t) else "") for t in wanted)
        try:
            cached = await ai._parse(f"<role>{role}</role>\n<topics>\n{lines}\n</topics>\n\n{HOOK_INSTRUCTIONS}",
                                     TopicHooks, effort="low", writing=True)
        except ai.AIError:
            return {}
        await cache.put("content-hooks", key, cached)
    out = {}
    for h in cached.hooks:
        match = next((t for t in wanted if canon(t) == canon(h.topic)), None)
        line = " ".join(h.hook.split())[:160]
        # numbers must come from the user, so a line that brings its own figure is dropped (counts like "3 things" stay)
        if match and line and not any(len(f.rstrip(".,")) > 1 or "%" in f for f in ai.new_figures(line, "")):
            out[match] = line
    return out


# ---- The plan (code) ----

def _zone(tz: str) -> ZoneInfo:
    try:
        return ZoneInfo(tz or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def _engagement(p: ContentPost) -> int:
    return p.likes + 3 * p.comments  # a comment takes more than a click, and LinkedIn weighs it more


def _median(xs: list[float]) -> float:
    return round(statistics.median(xs), 2) if xs else 0.0


def relative(posts: list[ContentPost]) -> dict[str, float]:
    """Each post's engagement against its author's usual (median), so a big account doesn't drown out the rest."""
    by_author: dict[str, list[ContentPost]] = {}
    for p in posts:
        by_author.setdefault(p.author_url, []).append(p)
    everyone = _median([_engagement(p) for p in posts]) or 1
    out = {}
    for items in by_author.values():
        base = _median([_engagement(p) for p in items]) if len(items) >= 3 else everyone
        for p in items:
            out[p.id] = round(_engagement(p) / max(base, 1), 3)
    return out


def _per_week(posts: list[ContentPost], now: datetime) -> float:
    recent = [p for p in posts if (now - datetime.fromisoformat(p.posted_at)).days <= CADENCE_DAYS]
    return round(len(recent) / (CADENCE_DAYS / 7), 1)


def _hours(start: int) -> str:
    return f"{start:02d}:00–{(start + 2) % 24:02d}:00"


def build_plan(role: str, pid: str, people: dict[str, list[ContentPost]], labels: dict[str, PostLabel], gaps: list[str],
               tz: str = "UTC", now: datetime | None = None, peer_names: list[str] | None = None) -> ContentPlan:
    """Everything in the plan except the first lines, from posts and labels alone. Deterministic."""
    now = now or datetime.now(timezone.utc)
    zone = _zone(tz)
    cutoff = now - timedelta(days=WINDOW_DAYS)
    own = [p for p in people.get(pid, []) if not p.engaged and datetime.fromisoformat(p.posted_at) >= cutoff]
    recent = {k: [p for p in v if datetime.fromisoformat(p.posted_at) >= cutoff] for k, v in people.items() if k != pid}
    peer_lists = {k: [p for p in v if not p.engaged] for k, v in recent.items()}  # what each peer wrote: cadence
    pool = {p.id: p for v in recent.values() for p in v if p.format != "repost"}  # plus what they engaged with
    peer_posts = list(pool.values())
    engaged = sum(p.engaged for p in peer_posts)
    rel = relative(peer_posts)

    # when: weekday x 2-hour block, in the viewer's time zone
    buckets: dict[tuple[int, int], list[float]] = {}
    for p in peer_posts:
        local = datetime.fromisoformat(p.posted_at).astimezone(zone)
        buckets.setdefault((local.weekday(), local.hour // 2 * 2), []).append(rel[p.id])
    slots = sorted(((k, _median(v), len(v)) for k, v in buckets.items() if len(v) >= MIN_BUCKET), key=lambda x: (-x[1], -x[2], x[0]))
    time_slots = [TimeSlot(weekday=DAYS[d], hours=_hours(h), posts=n, lift=lift) for (d, h), lift, n in slots[:6]]

    # how: format performance
    fmts: dict[str, list[float]] = {}
    for p in peer_posts:
        fmts.setdefault(p.format, []).append(rel[p.id])
    formats = sorted((FormatStat(format=f, posts=len(v), lift=_median(v)) for f, v in fmts.items() if len(v) >= MIN_BUCKET),
                     key=lambda s: (-s.lift, -s.posts, s.format))
    best = formats[0] if formats else None

    # what: topics, valued for engagement, how crowded they are, and the recruiter-searched skills they'd prove
    groups: dict[str, list[ContentPost]] = {}
    spell: dict[str, str] = {}
    for p in peer_posts:
        lab = labels.get(p.id)
        if lab and canon(lab.topic):
            groups.setdefault(canon(lab.topic), []).append(p)
            spell.setdefault(canon(lab.topic), lab.topic)
    n_peers = max(1, len({p.author_url for p in peer_posts}))  # everyone whose posts are in the sample
    gap_terms = [g for g in dict.fromkeys(" ".join(g.split())[:60] for g in gaps) if g][:15]
    topics, covered = [], set()
    for key, items in groups.items():
        authors = len({p.author_url for p in items})
        if len(items) < 2 or authors < MIN_TOPIC_AUTHORS:
            continue  # one person's or one company's subject isn't a pattern
        lift = _median([rel[p.id] for p in items])
        hay = _norm(" ".join([spell[key]] + [p.text for p in items]))
        proves = [g for g in gap_terms if keyword_hits([g, *variants(g)], hay)][:3]
        covered.update(canon(g) for g in proves)
        value = round(50 * min(max((lift - 0.5) / 1.5, 0), 1) + 20 * (1 - min(authors / n_peers, 1)) + (30 if proves else 0))
        example = max(items, key=lambda p: rel[p.id]).url
        topics.append(TopicIdea(topic=spell[key], posts=len(items), peers=authors, lift=lift, proves=proves, value=value, example_url=example))
    for g in gap_terms:  # skills recruiters search for that nobody here posts about: open ground that proves the skill
        if canon(g) not in covered and sum(t.lift is None for t in topics) < 3:
            topics.append(TopicIdea(topic=g, proves=[g], value=OPEN_GROUND_VALUE))
    topics.sort(key=lambda t: (-t.value, -t.posts, t.topic.lower()))
    topics = topics[:10]

    # benchmark
    rates = [_per_week(v, now) for v in peer_lists.values()]
    own_sorted = sorted(own, key=lambda p: p.posted_at, reverse=True)
    bench = Benchmark(
        active_peers=sum(1 for r in rates if r > 0), weekly_beats=sum(1 for r in rates if r < 1),
        peers=len(peer_lists), authors=n_peers if peer_posts else 0, peer_posts=len(peer_posts) - engaged, engaged_posts=engaged, peer_per_week=_median(rates), your_posts=len(own),
        your_per_week=_per_week(own, now) if pid in people else None,
        your_last_post_days=(now - datetime.fromisoformat(own_sorted[0].posted_at)).days if own_sorted else None,
        best_format=best.format if best else "", best_format_lift=best.lift if best else None,
    )

    # calendar: 2-3 posts a week on the best-performing days (distinct weekdays), topics in value order
    per_week = min(3, max(2, round(bench.peer_per_week)))
    measured = len(peer_posts) >= MIN_MEASURED and len(slots) >= per_week
    picked, used = [], set()
    if measured:
        for (d, h), lift, n in slots:
            if d not in used:
                used.add(d)
                picked.append((d, h, lift))
            if len(picked) == per_week:
                break
    else:
        picked = [(d, h, None) for d, h in DEFAULT_SLOTS[:per_week]]
    picked.sort()
    today = now.astimezone(zone).date()
    monday = today + timedelta(days=7 - today.weekday())
    calendar = []
    if topics:
        for w in range(WEEKS):
            for i, (d, h, lift) in enumerate(picked):
                t = topics[(w * len(picked) + i) % len(topics)]
                fmt_counts: dict[str, int] = {}
                for p in groups.get(canon(t.topic), []):
                    fmt_counts[p.format] = fmt_counts.get(p.format, 0) + 1
                fmt = max(fmt_counts, key=lambda f: (fmt_counts[f], f)) if fmt_counts else (best.format if best else "text")
                when = (f"peer posts at this time get {lift}× their usual engagement" if lift is not None
                        else "general LinkedIn guidance (too few peer posts to measure)")
                what = (f"shows {t.proves[0]}, which recruiters search for" if t.proves
                        else f"{t.lift}× usual engagement; {t.peers} of the {n_peers} people sampled post about it")
                calendar.append(CalendarSlot(date=(monday + timedelta(days=7 * w + d)).isoformat(), weekday=DAYS[d], time=_hours(h),
                                             topic=t.topic, format=fmt, value=t.value, why=f"{when[0].upper()}{when[1:]}; {what}."))
    return ContentPlan(status="ready", role=role, tz=str(zone.key), benchmark=bench, slots=time_slots, formats=formats,
                       topics=topics, calendar=calendar, peers=peer_names or [], measured=measured)


def preview(plan: ContentPlan) -> ContentPlan:
    """Free preview: the benchmark and the top 3 topics. The calendar is the full version."""
    return plan.model_copy(update={"topics": plan.topics[:3], "calendar": [], "slots": [], "formats": [], "preview": True})


async def peer_group(pid: str, role: str, country: str, allowed: bool) -> list[Peer] | None:
    """Up to PEERS people: same title in the same market first (they post about the role), then "people also viewed"
    (they're close to this profile). None when this request may not spend records and nothing is cached."""
    viewed = await peers_for(pid, allowed)
    if viewed is None:
        return None
    by_role = (await role_peers(role, country, {pid}, allowed))[:ROLE_PEERS]
    seen = {pid} | {profile_id(p.url) for p in by_role}
    return by_role + [p for p in viewed if profile_id(p.url) not in seen][:max(0, PEERS - len(by_role))]


async def plan_for(profile_url: str, role: str, gaps: list[str], tz: str, allowed: bool, country: str = "") -> ContentPlan:
    from . import ai
    pid = profile_id(profile_url)
    if not pid:
        return ContentPlan(status="unavailable", message="Add your LinkedIn profile link, like https://www.linkedin.com/in/your-name/")
    if ai.demo_mode():
        from .demo import demo_people
        peers, people = demo_people(pid)
    else:
        if not enabled():
            return ContentPlan(status="unavailable", message="The content plan isn't available right now.")
        try:
            peers = await peer_group(pid, role, country, allowed)
            if peers is None:
                return ContentPlan(status="locked", message="The content plan is part of the full version.")
            people = await posts_for(pid, [Peer(name="", url=_url(pid)), *peers], allowed)
        except Collecting:
            return ContentPlan(status="collecting", message="Reading posts from people like you on LinkedIn. This takes a minute or two.")
        except Unavailable as e:
            return ContentPlan(status="unavailable", message=str(e))
        except (httpx.HTTPError, ValueError, KeyError) as e:
            log.warning("Content plan collection failed: %s", type(e).__name__)
            return ContentPlan(status="unavailable", message="Couldn't read LinkedIn just now. Try again in a few minutes.")
        if not allowed and len(people) < len(peers) + 1:
            return ContentPlan(status="locked", message="The content plan is part of the full version.")
    labels = await labels_for([p for v in people.values() for p in v])
    plan = build_plan(role, pid, people, labels, gaps, tz, peer_names=[p.name for p in peers if p.name])
    plan.role_peers = sum(p.source == "role" for p in peers)
    plan.market = country
    if not plan.benchmark or not (plan.benchmark.peer_posts + plan.benchmark.engaged_posts):
        plan.message = "The people similar to you haven't posted publicly lately, so there's nothing to measure yet."
    if not allowed:
        return preview(plan)
    try:
        hooks = await asyncio.wait_for(hooks_for(role, plan.calendar, plan.topics), HOOK_BUDGET_S)
    except asyncio.TimeoutError:
        log.warning("Writing first lines ran past %ss; the calendar goes out without them", HOOK_BUDGET_S)
        hooks = {}
    plan.calendar = [s.model_copy(update={"hook": hooks.get(s.topic, "")}) for s in plan.calendar]
    return plan
