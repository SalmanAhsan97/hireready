import asyncio

from app import ai
from app.models import RoleKeywords


def _run(monkeypatch, paid: bool, writing: bool, premium_fails: bool = False) -> list[str]:
    calls = []

    async def fake_gemini(content, schema, effort, models=None):
        model = (models or [ai.GEMINI_MODEL])[0]
        calls.append(model)
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
    return calls


def test_paid_writing_uses_the_premium_model(monkeypatch):
    assert _run(monkeypatch, paid=True, writing=True) == ["gemini-pro-latest"]


def test_extraction_uses_the_standard_model_on_every_plan(monkeypatch):
    # facts that feed the score come from the same model for everyone, so scores don't depend on the plan
    assert _run(monkeypatch, paid=True, writing=False) == [ai.GEMINI_MODEL]
    assert _run(monkeypatch, paid=False, writing=True) == [ai.GEMINI_MODEL]


def test_busy_premium_model_falls_back(monkeypatch):
    assert _run(monkeypatch, paid=True, writing=True, premium_fails=True) == ["gemini-pro-latest", ai.GEMINI_MODEL]
