import asyncio
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import ai, demo, discover, jobs, main, scoring
from app.models import LinkedInProfile, PostingFacts, PostingsFacts, ProfileExperience, RoleKeyword, SearchMap, SearchTerm

client = TestClient(main.app)


def _posting(title, description):
    return SimpleNamespace(title=title, description=description)


def _facts(i, title="Marketing Manager", industry="SaaS", skills=(), tools=(), outcomes=()):
    return PostingFacts(index=i, title=title, industry=industry, skills=list(skills), tools=list(tools), outcomes=list(outcomes))


def _profile(**kw):
    base = dict(
        name="Jane Doe", headline="Marketing Manager | SEO and paid social for SaaS", about="I run SEO for SaaS brands.",
        location="Toronto, Canada",
        experiences=[ProfileExperience(title="Marketing Manager", company="Acme", description="Ran SEO and HubSpot campaigns.", start="2021", end="Present"),
                     ProfileExperience(title="Growth Marketer", company="Beta", description="Ran email campaigns.", start="2019", end="2021")],
        skills=["Google Ads"], skills_total=10, certifications=0, recommendations=0, education=1,
        connections=300, has_photo=True, has_banner=True, has_featured=True, custom_url=True,
    )
    return LinkedInProfile(**{**base, **kw})


def _map(*terms):
    return SearchMap(role="Marketing Manager", postings=10, terms=[SearchTerm(term=t, kind=k, label=l, share=s) for t, k, l, s in terms])


# ---- Search map ----

def test_search_map_counts_postings_and_labels_terms():
    postings = [_posting("Marketing Manager", "SEO and HubSpot") for _ in range(4)] + \
               [_posting("Digital Marketing Manager", "Paid social") for _ in range(4)] + \
               [_posting("Marketing Lead", "Events") for _ in range(2)]
    facts = [_facts(i, skills=["SEO"], tools=["HubSpot"]) for i in range(1, 5)] + \
            [_facts(i, title="Digital Marketing Manager", skills=["paid social"]) for i in range(5, 9)] + \
            [_facts(i, title="Marketing Lead", skills=["events"]) for i in (9, 10)]
    smap = discover.build_search_map("Marketing Manager", "Canada", postings, facts)
    by = {t.term: t for t in smap.terms}
    assert smap.postings == 10
    assert by["SEO"].share == 40 and by["SEO"].label == "useful"
    assert by["events"].share == 20 and by["events"].label == "useful"
    assert by["SaaS"].kind == "industry" and by["SaaS"].label == "essential"
    # "Marketing Manager" is literally in 8 posting titles, including the "Digital ..." ones
    assert by["Marketing Manager"].kind == "title" and by["Marketing Manager"].count == 8


def test_search_map_counts_literal_mentions_the_model_missed_and_merges_synonyms():
    postings = [_posting("Engineer", "Kubernetes and Python"), _posting("Engineer", "We run k8s"), _posting("Engineer", "Python only")]
    facts = [_facts(1, title="Engineer", tools=["Kubernetes"]), _facts(2, title="Engineer", tools=["K8s"]), _facts(3, title="Engineer", tools=["Python"])]
    smap = discover.build_search_map("Engineer", "USA", postings, facts)
    kube = [t for t in smap.terms if t.term.lower() in ("kubernetes", "k8s")]
    assert len(kube) == 1 and kube[0].count == 2  # one term, counted once per posting
    python = next(t for t in smap.terms if t.term == "Python")
    assert python.count == 2  # posting 1 lists only Kubernetes, but its text says Python
    assert python.label == "essential"


def test_search_map_ignores_facts_for_unknown_postings():
    smap = discover.build_search_map("X", "", [_posting("X", "")], [_facts(1, title="X"), _facts(7, title="X", skills=["SQL"])])
    assert "SQL" not in {t.term for t in smap.terms}


def test_estimated_map_is_the_fallback_and_marked_as_such():
    smap = discover.estimated_map("Marketing Manager", [RoleKeyword(term="SEO", aliases=["search engine optimization"])])
    assert smap.postings == 0 and smap.terms[0].label == "expected"


def test_scored_terms_weight_essential_double_and_drop_noise_and_titles():
    smap = _map(("SEO", "skill", "essential", 60), ("HubSpot", "tool", "useful", 30), ("Events", "skill", "noise", 10),
                ("Marketing Manager", "title", "essential", 80))
    assert [(t.term, w) for t, w in discover.scored_terms(smap)] == [("SEO", 2), ("HubSpot", 1)]


def test_keyword_coverage_is_weighted_and_names_the_posting_count():
    smap = _map(("SEO", "skill", "essential", 60), ("Salesforce", "tool", "useful", 30))
    weighted = discover.scored_terms(smap)
    card = scoring.compute_profile(_profile(), "Marketing Manager", [t for t, _ in weighted],
                                   weights={t.term: w for t, w in weighted}, postings=12)
    kw = next(c for c in card.categories if c.name == "Keyword coverage")
    assert kw.score == 67  # SEO (weight 2) found, Salesforce (weight 1) missing
    assert "12 recent postings" in kw.detail


# ---- Evidence map ----

def test_evidence_map_places_and_status():
    smap = _map(("SEO", "skill", "essential", 60), ("Google Ads", "tool", "useful", 30), ("A/B testing", "skill", "useful", 25),
                ("Events", "skill", "noise", 10))
    rows = {r.term: r for r in discover.evidence_map(_profile(), smap)}
    assert rows["SEO"].status == "proven" and rows["SEO"].places[:2] == ["Headline", "About"]
    assert "Experience — Acme" in rows["SEO"].places
    assert rows["Google Ads"].status == "claimed" and rows["Google Ads"].places == ["Skills"]
    assert rows["A/B testing"].status == "missing" and rows["A/B testing"].places == []
    assert "Events" not in rows  # noise isn't mapped


# ---- Recruiter searches ----

def test_recruiter_searches_are_deterministic_and_at_most_five():
    smap = _map(("Marketing Manager", "title", "essential", 80), ("Growth Marketing Manager", "title", "useful", 30),
                ("SEO", "skill", "essential", 70), ("HubSpot", "tool", "essential", 60), ("Salesforce", "tool", "useful", 30),
                ("SaaS", "industry", "useful", 40))
    a = discover.recruiter_searches("Marketing Manager", smap)
    assert a == discover.recruiter_searches("Marketing Manager", smap)
    assert [n for n, *_ in a] == ["Title + top skills", "Title variant + top skill", "Title + industry", "Skills only", "Any title variant"]


def test_search_appears_only_when_every_part_matches_and_ranks_by_field():
    smap = _map(("SEO", "skill", "essential", 70), ("HubSpot", "tool", "essential", 60), ("Salesforce", "tool", "useful", 30))
    fields = discover.profile_fields(_profile())
    hit = discover.run_search("t", ["Marketing Manager"], ["SEO"], "AND", fields, smap)
    assert hit.appears and hit.strength == "high" and hit.query == '"Marketing Manager" AND SEO'
    weak = discover.run_search("t", ["Marketing Manager"], ["HubSpot"], "AND", fields, smap)
    assert weak.appears and weak.strength == "low"  # HubSpot is only in an experience description
    miss = discover.run_search("t", ["Marketing Manager"], ["Salesforce"], "AND", fields, smap)
    assert not miss.appears and miss.strength == "none" and any("isn’t on your profile" in r for r in miss.reasons)


def test_title_variants_or_search_needs_one_title():
    fields = discover.profile_fields(_profile())
    smap = _map()
    any_hit = discover.run_search("t", ["Brand Manager", "Growth Marketer"], [], "OR", fields, smap)
    assert any_hit.appears and any_hit.strength == "low"  # Growth Marketer is a past title, not the current one
    assert not discover.run_search("t", ["Brand Manager"], [], "OR", fields, smap).appears


# ---- Rewrite guards ----

def test_rewrite_warnings_flag_cliches_new_figures_and_unproven_skills():
    known = "Grew signups 40% at Acme"
    clean = discover.rewrite_warnings("Marketing Manager | Grew signups 40% with SEO [X%]", known, ["Salesforce"])
    assert clean == []
    bad = discover.rewrite_warnings("Results-driven Marketing Manager who grew revenue 300% using Salesforce", known, ["Salesforce"])
    assert len(bad) == 3 and "300%" in bad[1] and "Salesforce" in bad[2]


def test_analyze_profile_attaches_checked_rewrites_in_demo():
    a = asyncio.run(ai.analyze_profile(LinkedInProfile.model_validate(demo.DEMO_LI_PROFILE), "Product Designer"))
    d = a.discover
    assert d.search_map.postings == len(demo.DEMO_POSTINGS)
    assert len(d.top_half) == 3 and d.experience_rewrites and len(d.searches) == 5
    asked = {discover.canon(e.term) for e in d.evidence if e.status != "proven"}
    assert all(discover.canon(q.term) in asked for q in d.proof_questions)


# ---- Search appearances ----

def test_compare_appearances():
    r = discover.compare_appearances("Senior Marketing Manager\nSales Executive, Marketing Coordinator", ["Marketing Manager", "Growth Marketing Manager"])
    assert r.on_target == ["Senior Marketing Manager"]
    assert r.partial == ["Marketing Coordinator"] and r.off_target == ["Sales Executive"]
    assert r.missing_targets == ["Growth Marketing Manager"]
    assert discover.compare_appearances("", ["Marketing Manager"]).on_target == []
    # sharing only the role noun isn't close
    assert discover.compare_appearances("Graphic Designer", ["Product Designer"]).off_target == ["Graphic Designer"]


def test_appearances_endpoint():
    r = client.post("/api/linkedin/appearances", json={"pasted": "Product Designer; Graphic Designer", "targets": ["Product Designer"]})
    assert r.status_code == 200 and r.json()["on_target"] == ["Product Designer"]


# ---- Fetching postings for the map ----

def test_guess_country():
    assert ai.guess_country("Toronto, Ontario, Canada") == "Canada"
    assert ai.guess_country("London, England, United Kingdom") == "UK"
    assert ai.guess_country("") == "USA"


@pytest.fixture
def live(monkeypatch):
    monkeypatch.setattr(ai, "demo_mode", lambda: False)


def test_search_map_falls_back_when_postings_fail(monkeypatch, live):
    async def boom(*a, **k):
        raise jobs.JobSearchError("blocked")
    monkeypatch.setattr(jobs, "search", boom)
    assert asyncio.run(ai.search_map("Fallback Role", "USA")) is None


def test_search_map_needs_enough_postings(monkeypatch, live):
    async def few(*a, **k):
        return [_posting("X", "")] * (discover.MIN_POSTINGS - 1)
    monkeypatch.setattr(jobs, "search", few)
    assert asyncio.run(ai.search_map("Few Role", "USA")) is None


def test_search_map_reads_postings_once_then_uses_the_cache(monkeypatch, live):
    postings = [_posting("Data Analyst", "SQL and Tableau")] * 6
    calls = []

    async def found(*a, **k):
        return postings

    async def parse(content, schema, effort, writing=False):
        calls.append(content)
        return PostingsFacts(postings=[_facts(i, title="Data Analyst", tools=["SQL"]) for i in range(1, 7)])

    monkeypatch.setattr(jobs, "search", found)
    monkeypatch.setattr(ai, "_parse", parse)
    first = asyncio.run(ai.search_map("Data Analyst", "Canada"))
    second = asyncio.run(ai.search_map("Data Analyst", "Canada"))
    assert len(calls) == 1 and first == second
    assert next(t for t in first.terms if t.term == "SQL").label == "essential"


# ---- API ----

def test_linkedin_demo_review_returns_discoverability():
    r = client.post("/api/linkedin", data={"profile_url": "https://www.linkedin.com/in/demo/", "target_role": "Product Designer"})
    assert r.status_code == 200
    d = r.json()["analysis"]["discover"]
    assert d["search_map"]["postings"] == len(demo.DEMO_POSTINGS) and d["searches"] and d["top_half"]


def test_linkedin_preview_locks_the_rewrites(monkeypatch):
    monkeypatch.setattr(main, "ACCESS_CODE", "secret-code")
    body = client.post("/api/linkedin", data={"profile_url": "https://www.linkedin.com/in/demo/"}).json()
    d = body["analysis"]["discover"]
    assert body["preview"] and d["searches"] and d["top_half"] == [] and d["experience_rewrites"] == []
    assert body["locked"]["rewrites"] > 0


def test_linkedin_rejects_unknown_country():
    r = client.post("/api/linkedin", data={"profile_url": "https://www.linkedin.com/in/demo/", "country": "Atlantis"})
    assert r.status_code == 400
