import asyncio

from app import ai
from app.models import RoleKeywords


def _run(monkeypatch, paid: bool, writing: bool, premium_fails: bool = False) -> list[str]:
    calls, modes = [], []

    async def fake_gemini(content, schema, effort, models=None, writing=False):
        model = (models or [ai.GEMINI_MODEL])[0]
        calls.append(model)
        modes.append(writing)
        if premium_fails and model == "gemini-pro-latest":
            raise ai.AIError("busy")
        return RoleKeywords(keywords=[])

    monkeypatch.setenv("GEMINI_API_KEY", "test")
    monkeypatch.setattr(ai, "PREMIUM_MODEL", "gemini-pro-latest")
    monkeypatch.setattr(ai, "_gemini_parse", fake_gemini)

    async def go():
        ai.set_plan(paid)
        await ai._parse("x", RoleKeywords, effort="low", writing=writing)

    asyncio.run(go())
    _run.modes = modes
    return calls


def test_paid_writing_uses_the_premium_model(monkeypatch):
    assert _run(monkeypatch, paid=True, writing=True) == ["gemini-pro-latest"]


def test_extraction_uses_the_standard_model_on_every_plan(monkeypatch):
    # facts that feed the score come from the same model for everyone, so scores don't depend on the plan
    assert _run(monkeypatch, paid=True, writing=False) == [ai.GEMINI_MODEL]
    assert _run.modes == [False]  # facts are read at temperature 0
    assert _run(monkeypatch, paid=False, writing=True) == [ai.GEMINI_MODEL]


def test_busy_premium_model_falls_back(monkeypatch):
    assert _run(monkeypatch, paid=True, writing=True, premium_fails=True) == ["gemini-pro-latest", ai.GEMINI_MODEL]


def test_first_read_of_a_pair_is_voted_then_cached(monkeypatch, tmp_path):
    from app import cache
    from app.models import Evidence, Extraction, JobRequirements, Requirement, ResumeEvidence, Job
    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path)
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    reqs = JobRequirements(job_title="Designer", company="Acme", requirements=[
        Requirement(id="r1", kind="skill", text="Figma", importance="must", keywords=["Figma"], min_years=None)])
    verdicts = iter(["met", "partial", "met", "missing", "missing", "missing"])
    calls = []

    async def fake_parse(content, schema, effort, writing=False):
        calls.append(schema.__name__)
        ev = ResumeEvidence(candidate_name="X", evidence=[Evidence(id="r1", status=next(verdicts), quote="", years=None)])
        return Extraction(job=reqs, resume=ev) if schema is Extraction else ev

    monkeypatch.setattr(ai, "_parse", fake_parse)
    monkeypatch.setattr(ai, "EVIDENCE_VOTES", 3)
    job = Job(description="Designer at Acme. Figma required.")
    _, ev = asyncio.run(ai.extract("Resume text with Figma", job))
    assert calls == ["Extraction", "ResumeEvidence", "ResumeEvidence"]  # 1 full read + 2 extra verdicts
    assert ev.evidence[0].status == "met"  # met, partial, met -> met
    asyncio.run(ai.extract("Resume text with Figma", job))
    assert len(calls) == 3  # cached: no new calls for the same pair
    _, ev2 = asyncio.run(ai.extract("Another resume", job))
    assert calls[3:] == ["ResumeEvidence"] * 3 and ev2.evidence[0].status == "missing"  # new pair, known job: 3 votes
