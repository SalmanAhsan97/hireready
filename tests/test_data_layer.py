"""The optional data layer (posting library, calibration, fact vault, importer).

Hard rule under test: with no imported data, an empty store or Supabase down, every score, keyword list,
search map and band is exactly what the built-in engine gives. All data here is synthetic.
"""
import asyncio
import io
import json
import zipfile
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app import ai, cache, calibration, demo, discover, importer, jobs, library, linkedin_jobs, main, scoring, vault
from app.models import (ImportApplication, ImportBatch, ImportPosting, ImportVaultItem, Job, LinkedInProfile, PostingFacts,
                        PostingsFacts, RoleKeyword, RoleKeywords)

DAY = date.today()


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def fresh(monkeypatch, tmp_path):
    """Own disk store per test, no Supabase, and no memoized library/calibration/vault reads."""
    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path)
    monkeypatch.setenv("SUPABASE_URL", "")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "")
    library._memo.clear()
    monkeypatch.setattr(calibration, "_active", None)
    monkeypatch.setattr(vault, "_memo", None)
    yield
    library._memo.clear()


def store(table, rows):
    for r in rows:
        r.setdefault("created_at", "2026-10-01T00:00:00+00:00")
    run(cache.data_insert(table, rows))


def library_posting(i, title="Product Designer", terms=("Figma", "Webflow"), days_old=10, market="Canada"):
    return {"content_hash": f"p{i}-{title}", "title": title, "title_norm": scoring._norm(title), "company": f"Co {i}",
            "location": "Toronto", "market": market, "posted_on": (DAY - timedelta(days=days_old)).isoformat(),
            "description": f"{title} role. " + ", ".join(terms) + ". Work with product and engineering.", "terms": list(terms)}


def all_outputs():
    """Job match, resume-only and LinkedIn review, as the API would return them."""
    li = LinkedInProfile.model_validate(demo.DEMO_LI_PROFILE)
    return [run(ai.analyze("x", Job(description="Senior Product Designer " * 100))).model_dump(mode="json"),
            run(ai.analyze_resume("x")).model_dump(mode="json"),
            run(ai.analyze_profile(li, "Product Designer", "Canada")).model_dump(mode="json")]


# ---- Identical output when the layer is empty or unreachable ----

def test_empty_layer_and_supabase_down_give_identical_output(monkeypatch):
    empty = all_outputs()
    assert all(o.get("keyword_basis", "") == "" for o in empty)
    assert empty[0]["assessment"]["bands"] == "" and empty[0]["standing"] == scoring.standing_for(empty[0]["overall_score"])

    # Supabase configured but unreachable: every data-layer read fails and reads as "no data"
    monkeypatch.setenv("SUPABASE_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "sb_secret_test")
    monkeypatch.setattr(cache, "_http", None)
    library._memo.clear()
    monkeypatch.setattr(calibration, "_active", None)
    try:
        down = all_outputs()
    finally:
        monkeypatch.setattr(cache, "_http", None)
    assert down == empty


def test_search_map_weights_none_equals_uniform_and_matches_built_in():
    postings = [jobs.Posting(title=t, description=d, url=f"https://x/{i}", site="indeed") for i, (t, d) in enumerate(demo.DEMO_POSTINGS)]
    facts = [PostingFacts.model_validate(f) for f in demo.DEMO_POSTING_FACTS]
    plain = discover.build_search_map("Product Designer", "Canada", postings, facts)
    assert discover.build_search_map("Product Designer", "Canada", postings, facts, [1.0] * len(postings)) == plain
    assert discover.build_search_map("Product Designer", "Canada", postings, facts, [1, 1]) == plain  # wrong length is ignored


def _live_map(monkeypatch, role, live):
    async def indeed(*a, **k):
        return live

    async def no_linkedin(*a, **k):
        return []

    async def facts(content, schema, effort, writing=False):  # the model's reading: tools named in each posting
        blocks = content.split("<posting ")[1:]
        return PostingsFacts(postings=[PostingFacts(index=i, title="Product Designer", industry="SaaS", skills=["user research"],
                                                    tools=[t for t in ("Figma", "Webflow") if t in b], outcomes=[])
                                       for i, b in enumerate(blocks, 1)])
    monkeypatch.setattr(jobs, "search", indeed)
    monkeypatch.setattr(linkedin_jobs, "search", no_linkedin)
    monkeypatch.setattr(ai, "_parse", facts)
    return run(ai.search_map(role, "Canada"))


def test_search_map_unchanged_with_empty_library(monkeypatch):
    live = [jobs.Posting(title=t, description=d, url=f"https://x/{i}", site="indeed") for i, (t, d) in enumerate(demo.DEMO_POSTINGS)]
    smap = _live_map(monkeypatch, "Product Designer", live)
    merged = ai.merge_postings(live)
    facts = [PostingFacts(index=i, title="Product Designer", industry="SaaS", skills=["user research"],
                          tools=["Figma"] if "Figma" in p.description else [], outcomes=[]) for i, p in enumerate(merged, 1)]
    expected = discover.build_search_map("Product Designer", "Canada", merged, facts)
    assert smap.terms == expected.terms and smap.postings == len(merged) and smap.sources == {"Indeed": len(merged)}


def test_library_postings_join_live_ones_weighted_by_recency(monkeypatch):
    store("postings", [library_posting(i, days_old=30 + i) for i in range(3)] +
          [library_posting(9, title="Marketing Manager"), library_posting(8, market="UK")])
    live = [jobs.Posting(title=t, description=d, company=f"Live {i}", url=f"https://x/{i}", site="indeed")
            for i, (t, d) in enumerate(demo.DEMO_POSTINGS[:4])]
    smap = _live_map(monkeypatch, "Senior Product Designer", live)
    assert smap.postings == 7 and smap.sources == {"Indeed": 4, "Library": 3}  # other roles and markets left out
    webflow = next(t for t in smap.terms if t.term == "Webflow")
    assert webflow.count == 3 and 0 < webflow.share < round(100 * 3 / 7)  # older library postings count for less


def test_library_never_replaces_live_postings(monkeypatch):
    store("postings", [library_posting(i) for i in range(10)])
    live = [jobs.Posting(title="Product Designer", description=f"Figma {i}", company=f"Live {i}", url=f"https://x/{i}", site="indeed")
            for i in range(ai.MAX_MAP_POSTINGS)]
    smap = _live_map(monkeypatch, "Product Designer", live)
    assert smap.sources == {"Indeed": ai.MAX_MAP_POSTINGS}


def test_recency_weight():
    assert library.recency_weight(DAY.isoformat()) == 1.0
    assert library.recency_weight((DAY - timedelta(days=library.HALF_LIFE_DAYS)).isoformat()) == 0.5
    assert library.recency_weight((DAY - timedelta(days=library.MAX_AGE_DAYS + 1)).isoformat()) == 0.0
    assert library.recency_weight("") == library.UNDATED_WEIGHT


# ---- Role keyword lists ----

BASE = [RoleKeyword(term=t, aliases=[]) for t in ("Figma", "Prototyping", "User research", "Sketch", "InVision")]


def _role_keywords(monkeypatch, role="Product Designer"):
    run(cache.put("roles", ai._key("role-keywords-v2", role.lower(), "mid"), RoleKeywords(keywords=BASE)))
    return run(ai.role_keywords_basis(role, "mid"))


def test_role_keywords_unchanged_below_minimum(monkeypatch):
    assert _role_keywords(monkeypatch) == (BASE, 0)
    store("postings", [library_posting(i) for i in range(library.MIN_POSTINGS - 1)])
    library._memo.clear()
    assert _role_keywords(monkeypatch) == (BASE, 0)


def test_role_keywords_lifted_once_enough_postings(monkeypatch):
    store("postings", [library_posting(i, terms=("Figma", "Webflow", "User research")) for i in range(library.MIN_POSTINGS)])
    words, used = _role_keywords(monkeypatch)
    assert used == library.MIN_POSTINGS and len(words) == len(BASE)
    assert [k.term for k in words] == ["Figma", "Prototyping", "User research", "Sketch", "Webflow"]  # InVision: no posting asks


def test_lift_keywords_is_identity_without_shares():
    assert library.lift_keywords(BASE, []) is BASE
    assert library.lift_keywords(BASE, [("Figma", 90)]) == BASE  # nothing new to add


# ---- Calibration ----

def pairs(score, callbacks, misses, pending=0):
    return [(score, "interview")] * callbacks + [(score, "rejected")] * misses + [(score, "applied")] * pending


def test_calibration_keeps_built_in_bands_below_minimum():
    few = pairs(85, 10, 5) + pairs(70, 5, 10, pending=4)
    assert calibration.fit(few) is calibration.BUILTIN
    rows = {r["band"]: r for r in calibration.report(few)}
    assert rows["top candidate"]["outcomes"] == 15 and rows["top candidate"]["rate"] == 67
    assert rows["competitive"]["pending"] == 4 and not rows["competitive"]["enough"]


def test_calibration_moves_a_cut_only_with_enough_outcomes_on_both_sides():
    data = pairs(90, 20, 10) + pairs(77, 25, 5) + pairs(70, 4, 26)  # 75-79 behaves like the top band
    bands = calibration.fit(data)
    assert bands.cuts == (75, 65, 45) and bands.version.startswith("calibrated 75/65/45")
    assert "callback" in calibration.wording("top candidate", bands)
    assert calibration.wording("long shot", bands) == ""  # no outcomes there: no claim


def test_calibrated_bands_reach_the_analysis(monkeypatch):
    builtin = run(ai.analyze("x", Job(description="Senior Product Designer " * 100)))

    async def outcomes():
        return pairs(builtin.overall_score, 40, 10) + pairs(10, 5, 40)
    monkeypatch.setattr(calibration, "outcome_pairs", outcomes)
    monkeypatch.setattr(calibration, "_active", None)
    tuned = run(ai.analyze("x", Job(description="Senior Product Designer " * 100)))
    assert tuned.overall_score == builtin.overall_score  # calibration never touches the score itself
    assert tuned.assessment.bands.startswith("calibrated") and "callback 80%" in tuned.assessment.decision_reason


def test_calibration_failure_means_built_in(monkeypatch):
    async def boom():
        raise RuntimeError("store down")
    monkeypatch.setattr(calibration, "outcome_pairs", boom)
    assert run(calibration.active(force=True)) is calibration.BUILTIN


# ---- Fact vault ----

VAULT = [
    ImportVaultItem(kind="removed", claim="4x ROAS on paid social", figure="4x", status="removed", applies_to="Social Media Manager"),
    ImportVaultItem(kind="limit", claim="Canadian Permanent Resident", status="verified", max_mentions=1),
    ImportVaultItem(kind="figure", claim="Grew newsletter list 35%", figure="35%", status="unverified"),
    ImportVaultItem(kind="figure", claim="Managed $2M budget", figure="$2M", status="verified"),
]


def test_vault_flags_scoped_figures_and_limits():
    text = "Drove 4x ROAS. Grew the list 35%. Managed $2M. Canadian PR. Permanent resident of Canada."
    assert vault.flagged_figures(text, VAULT, "Senior Social Media Manager") == ["35%", "4x"]
    assert vault.flagged_figures(text, VAULT, "Content Marketing Manager") == ["35%"]  # 4x rule is scoped
    warns = vault.warnings("cover letter", text, VAULT, "Social Media Manager")
    assert any("4x" in w for w in warns) and any("at most 1" in w and "2 times" in w for w in warns)
    assert vault.warnings("cover letter", text, [], "Social Media Manager") == []


def test_new_figures_unchanged_without_vault():
    text, known = "Grew 35% and 4x", "Grew 35% and 4x"
    assert ai.new_figures(text, known) == ai.new_figures(text, known, None) == []
    assert ai.new_figures(text, known, VAULT, "Social Media Manager") == ["35%", "4x"]


def test_visitors_never_read_the_vault(monkeypatch):
    async def must_not_run():
        raise AssertionError("vault read for a visitor")
    monkeypatch.setattr(vault, "entries", must_not_run)
    ai.set_owner(False)
    assert run(ai._vault()) == []


def test_owner_reads_the_vault(monkeypatch):
    store("vault", [{"content_hash": "v1", **VAULT[0].model_dump()}])

    async def owner():
        ai.set_owner(True)
        return await ai._vault()
    assert [e.figure for e in run(owner())] == ["4x"]


# ---- Importer ----

def chatgpt_zip(convs):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("export/conversations.json", json.dumps(convs))
    return buf.getvalue()


def conversation(title, texts, gizmo=""):
    mapping = {f"m{i}": {"message": {"author": {"role": "user" if i % 2 == 0 else "assistant"}, "create_time": i,
                                     "content": {"content_type": "text", "parts": [t]}}} for i, t in enumerate(texts)}
    return {"title": title, "gizmo_id": gizmo, "mapping": {"root": {"message": None}, **mapping}}


SYNTH_BATCH = ImportBatch(
    postings=[ImportPosting(title="Social Media Manager", company="Acme", location="Toronto, Canada", posted_on="2026-09-03",
                            description="We need a Social Media Manager. Email jobs@acme.test or call +1 416 555 0199. " + "Paid social, Meta Ads. " * 30,
                            terms=["Meta Ads", "Paid social"])],
    applications=[ImportApplication(role="Social Media Manager", company="Acme", applied_on="2026-09-05", outcome="interview",
                                    job_text="Posting " * 100, resume_text="Resume " * 100),
                  ImportApplication(role="Brand Manager", company="Beta", outcome="rejected")],
    vault=[ImportVaultItem(kind="removed", claim="4x ROAS", figure="4x", status="verified", applies_to="Social Media Manager"),
           ImportVaultItem(kind="limit", claim="Canadian PR", status="verified")],  # limit without max_mentions: dropped
)


def test_conversations_filtered_by_relevance_and_project():
    convs = [conversation("Resume for Acme", ["Here is the job posting", "Thanks"], gizmo="g-p-career"),
             conversation("Pasta recipe", ["How long to boil", "10 minutes"], gizmo="g-p-career"),
             conversation("Cover letter", ["Draft my cover letter", "Sure"], gizmo="g-p-other")]
    assert len(importer.conversations_text(convs)) == 2
    texts = importer.conversations_text(convs, project="g-p-career")
    assert len(texts) == 1 and texts[0].startswith("# Resume for Acme") and "[assistant] Thanks" in texts[0]


def test_import_chatgpt_zip_validates_stores_and_is_idempotent():
    calls = []

    async def parse(text):
        calls.append(text)
        return SYNTH_BATCH
    data = chatgpt_zip([conversation("Job search", ["My resume and the job posting for Acme", "Noted"])])
    result = run(importer.import_bytes("export.zip", data, parse=parse))
    assert result["kind"] == "chatgpt" and len(calls) == 1
    assert (result["postings_new"], result["outcomes_new"], result["vault_new"]) == (1, 1, 1)
    assert result["applications_found"] == 2 and result["applications_scorable"] == 1

    posting = run(cache.data_rows("postings"))[0]
    assert posting["market"] == "Canada" and posting["posted_on"] == "2026-09-03"
    assert "jobs@acme.test" not in posting["description"] and "555 0199" not in posting["description"]
    outcome = run(cache.data_rows("outcomes"))[0]
    assert outcome["outcome"] == "interview" and 0 <= outcome["score"] <= 100
    assert "Resume" not in json.dumps(outcome)  # only the score and outcome are kept
    assert run(cache.data_rows("vault"))[0]["status"] == "removed"

    again = run(importer.import_bytes("export.zip", data, parse=parse))
    assert again["skipped"] and len(calls) == 1


def test_structured_json_imports_without_a_model():
    data = SYNTH_BATCH.model_dump_json().encode()
    result = run(importer.import_bytes("batch.json", data))
    assert result["kind"] == "structured" and result["postings_new"] == 1


def test_markdown_needs_a_model_in_demo_mode():
    with pytest.raises(importer.ImportFailed):
        run(importer.import_bytes("handover.md", b"# Handover\n\nResume notes and the job posting for Acme."))


def test_chunks_respect_size():
    parts = importer.chunks(["a" * 30000, "b\n\nc"], size=12000)
    assert all(len(p) <= 12000 for p in parts) and parts[-1] == "b\n\nc"


# ---- Admin endpoints ----

@pytest.fixture
def admin(monkeypatch):
    monkeypatch.setattr(main, "ADMIN_USER", "owner")
    monkeypatch.setattr(main, "ADMIN_PASSWORD", "test-pass")
    main._login_failures.clear()
    return TestClient(main.app)


def test_data_endpoints_are_admin_only(admin):
    assert admin.get("/api/admin/data").status_code == 401
    assert admin.post("/api/admin/import", files={"file": ("b.json", b"{}", "application/json")}).status_code == 401
    admin.post("/api/admin/login", json={"username": "owner", "password": "test-pass"})
    r = admin.post("/api/admin/import", files={"file": ("b.json", SYNTH_BATCH.model_dump_json().encode(), "application/json")})
    assert r.status_code == 200 and r.json()["postings_new"] == 1
    d = admin.get("/api/admin/data").json()
    assert d["postings"]["total"] == 1 and d["calibration"]["version"] == "builtin"
    assert [b["band"] for b in d["calibration"]["bands"]] == list(calibration.BANDS)
    assert admin.post("/api/admin/import", files={"file": ("x.pdf", b"%PDF", "application/pdf")}).status_code == 422
