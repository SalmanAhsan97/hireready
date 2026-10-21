"""Content plan: Bright Data records → posts, the plan computed in code, spending rules. All data is synthetic."""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import ai, cache, content, main
from app.models import ContentPost, PostLabel

NOW = datetime(2026, 10, 21, 12, tzinfo=timezone.utc)  # a Wednesday


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def own_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path)


def post(i, author, days_ago, hour, likes, fmt="text", text="", comments=0):
    when = (NOW - timedelta(days=days_ago)).replace(hour=hour)
    return ContentPost(id=f"{author}-{i}", author=author, author_url=f"https://www.linkedin.com/in/{author}/",
                       posted_at=when.isoformat(), likes=likes, comments=comments, format=fmt, text=text, url=f"https://x/{author}/{i}")


def peers_posts():
    """4 peers × 8 posts. Tuesday 14:00 UTC posts get 3× the usual; design-systems carousels do best."""
    people = {}
    for a, name in enumerate(["ana", "ben", "cai", "dee"]):
        items = []
        for i in range(8):
            days = 1 + 7 * i  # Tuesdays
            tuesday = i % 2 == 0
            topic_text = "Our design system tokens" if i % 2 == 0 else "A career lesson"
            items.append(post(i, name, days if tuesday else days + 2, 14 if tuesday else 18, (300 if tuesday else 100) * (a + 1),
                              "document" if tuesday else "text", topic_text, comments=5 * (a + 1)))
        people[name] = items
    return people


def labels_for(people):
    return {p.id: PostLabel(id=p.id, topic="design systems" if "design" in p.text else "career lesson", hook="list")
            for v in people.values() for p in v}


def test_record_parsing_formats_and_peers():
    rec = {"id": "1", "date_posted": "2026-10-16T15:29:38.640Z", "num_likes": "821", "num_comments": 10, "post_type": "post",
           "document_page_count": 8, "post_text": "Hello", "url": "https://www.linkedin.com/posts/x"}
    p = content.post_from_record(rec, "Ana", "https://www.linkedin.com/in/ana/")
    assert p.format == "document" and p.likes == 821 and p.posted_at.startswith("2026-10-16T15:29")
    assert content.post_from_record({"post_type": "repost", "date_posted": "2026-10-16T00:00:00Z"}, "", "").format == "repost"
    assert content.post_from_record({"error": "x"}, "", "") is None
    profile = {"people_also_viewed": [{"profile_link": "https://www.linkedin.com/in/ana", "name": "Ana"},
                                      {"profile_link": "https://www.linkedin.com/in/me", "name": "Me"}],
               "similar_profiles": [{"url": "https://ca.linkedin.com/in/ana/", "name": "Ana"}, {"url": "https://www.linkedin.com/in/ben", "name": "Ben"}]}
    assert [p.name for p in content.peers_from_profile(profile, "me")] == ["Ana", "Ben"]  # self and duplicates dropped


def test_plan_finds_best_time_format_and_topics():
    people = peers_posts()
    people["me"] = [post(0, "me", 47, 9, 20)]
    plan = content.build_plan("Product Designer", "me", people, labels_for(people), ["design systems", "accessibility"], "UTC", NOW)
    assert plan.measured and plan.benchmark.peers == 4 and plan.benchmark.your_last_post_days == 47
    assert (plan.slots[0].weekday, plan.slots[0].hours) == ("Tuesday", "14:00–16:00") and plan.slots[0].lift > 1
    assert plan.formats[0].format == "document"
    top = plan.topics[0]
    assert top.topic == "design systems" and top.proves == ["design systems"]
    open_ground = next(t for t in plan.topics if t.topic == "accessibility")
    assert open_ground.lift is None and open_ground.value == content.OPEN_GROUND_VALUE < top.value
    assert len(plan.calendar) == 4 * 2 and all(c.date > "2026-10-21" for c in plan.calendar)
    assert plan.calendar[0].weekday == "Tuesday" and plan.calendar[0].time == "14:00–16:00"


def test_big_accounts_dont_skew_engagement():
    people = peers_posts()
    rel = content.relative([p for v in people.values() for p in v])
    # dee has 4× ana's likes, but the same pattern, so the same relative engagement
    assert rel["ana-0"] == rel["dee-0"] and rel["ana-1"] == rel["dee-1"]


def test_time_zone_shifts_the_slots():
    people = peers_posts()
    plan = content.build_plan("Product Designer", "me", people, labels_for(people), [], "Asia/Karachi", NOW)
    assert (plan.slots[0].weekday, plan.slots[0].hours) == ("Tuesday", "18:00–20:00") and plan.tz == "Asia/Karachi"
    assert content.build_plan("x", "me", people, {}, [], "Not/AZone", NOW).tz == "UTC"


def test_too_few_posts_falls_back_to_general_guidance():
    people = {"ana": [post(0, "ana", 3, 9, 50)], "ben": [post(0, "ben", 5, 9, 50)]}
    plan = content.build_plan("Designer", "me", people, labels_for(people), ["figma"], "UTC", NOW)
    assert not plan.measured and plan.calendar and plan.calendar[0].why.startswith("General LinkedIn guidance")
    assert plan.benchmark.your_per_week is None  # no posts fetched for "me" at all


def test_preview_drops_the_calendar():
    people = peers_posts()
    plan = content.preview(content.build_plan("x", "me", people, labels_for(people), [], "UTC", NOW))
    assert plan.preview and plan.calendar == [] and len(plan.topics) <= 3


def _fake_bright_data(monkeypatch):
    """Profile snapshot lists two peers; posts snapshot returns three posts each. Counts the triggers."""
    calls = []

    async def fake_run(kind, key, dataset, params, inputs, estimate):
        calls.append(kind)
        if kind == "peers":
            return [{"people_also_viewed": [{"profile_link": "https://www.linkedin.com/in/ana", "name": "Ana"},
                                            {"profile_link": "https://www.linkedin.com/in/ben", "name": "Ben"}]}]
        out = []
        for inp in inputs:
            for i in range(3):
                out.append({"id": f"{inp['url']}-{i}", "input": {"url": inp["url"]}, "num_likes": 10 * (i + 1), "num_comments": 1,
                            "date_posted": (datetime.now(timezone.utc) - timedelta(days=3 + i)).isoformat(), "post_text": "Design systems at work"})
        return out
    monkeypatch.setattr(content, "_run", fake_run)
    monkeypatch.setattr(ai, "demo_mode", lambda: False)
    monkeypatch.setenv("BRIGHTDATA_API_KEY", "test-key")

    async def labels(posts):
        return {p.id: PostLabel(id=p.id, topic="design systems", hook="list") for p in posts}
    monkeypatch.setattr(content, "labels_for", labels)

    async def hooks(role, slots, topics):
        return {}
    monkeypatch.setattr(content, "hooks_for", hooks)
    return calls


def test_free_visitors_never_spend_records_and_peer_posts_are_shared(monkeypatch):
    calls = _fake_bright_data(monkeypatch)
    url = "https://www.linkedin.com/in/me/"
    assert run(content.plan_for(url, "Designer", [], "UTC", allowed=False)).status == "locked"
    assert calls == []
    plan = run(content.plan_for(url, "Designer", [], "UTC", allowed=True))
    assert plan.status == "ready" and plan.benchmark.peers == 2 and plan.benchmark.peer_posts == 6 and calls == ["peers", "posts"]
    again = run(content.plan_for(url, "Designer", [], "UTC", allowed=False))  # cached now: a free preview, no new records
    assert again.preview and calls == ["peers", "posts"]


def test_collecting_status_when_a_snapshot_is_still_running(monkeypatch):
    _fake_bright_data(monkeypatch)

    async def still_running(*a, **k):
        raise content.Collecting()
    monkeypatch.setattr(content, "_run", still_running)
    assert run(content.plan_for("https://www.linkedin.com/in/me/", "x", [], "UTC", allowed=True)).status == "collecting"


def test_endpoint_in_demo_mode():
    client = TestClient(main.app)
    r = client.post("/api/linkedin/content", json={"profile_url": "https://www.linkedin.com/in/demo-user/", "role": "Product Designer",
                                                   "gaps": ["accessibility"], "tz": "America/Toronto"})
    plan = r.json()["plan"]
    assert r.status_code == 200 and plan["status"] == "ready" and plan["calendar"] and plan["calendar"][0]["hook"]
    bad = client.post("/api/linkedin/content", json={"profile_url": "not a link"}).json()["plan"]
    assert bad["status"] == "unavailable"
