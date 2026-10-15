import pytest
from fastapi.testclient import TestClient

from app import demo, main

client = TestClient(main.app)
RESUME = ("resume.txt", demo.DEMO_RESUME.encode(), "text/plain")


@pytest.fixture
def access_code(monkeypatch):
    monkeypatch.setattr(main, "ACCESS_CODE", "secret-code")


def test_status():
    s = client.get("/api/status").json()
    assert s["demo"] is True and s["storage"] == "disk"


def test_index_versions_static_assets():
    html = client.get("/").text
    assert "/static/app.js?v=" in html and "/static/style.css?v=" in html


def test_resume_only_when_no_job_given():
    r = client.post("/api/analyze", files={"resume": RESUME})
    assert r.status_code == 200
    assert r.json()["analysis"]["mode"] == "resume"


def test_job_match_with_pasted_description():
    r = client.post("/api/analyze", files={"resume": RESUME}, data={"job_text": "Senior Product Designer at Acme Cloud"})
    body = r.json()
    assert r.status_code == 200 and body["analysis"]["mode"] == "job" and body["analysis"]["requirements"]


def test_preview_without_code_removes_locked_parts_server_side(access_code):
    body = client.post("/api/analyze", files={"resume": RESUME}, data={"job_text": "Senior Product Designer"}).json()
    a = body["analysis"]
    assert body["preview"] is True
    assert len(a["changes"]) <= 1 and a["missing_keywords"] == [] and a["requirements"] == []
    assert not any(c["rationale"] for c in a["categories"])
    assert body["locked"]["requirements"] > 0


def test_full_report_with_code(access_code):
    body = client.post("/api/analyze", files={"resume": RESUME}, data={"job_text": "x"}, headers={"X-Access-Code": "secret-code"}).json()
    assert body["preview"] is False and body["analysis"]["requirements"]


def test_wrong_code_is_rejected(access_code):
    r = client.post("/api/analyze", files={"resume": RESUME}, headers={"X-Access-Code": "nope"})
    assert r.status_code == 401


def test_full_version_features_need_the_code(access_code):
    chat = client.post("/api/resume-chat", json={"resume_text": "x", "messages": [{"role": "user", "content": "hi"}]})
    docx = client.post("/api/export/docx", json={"markdown": "# x"})
    pdf = client.post("/api/export/pdf", json={"markdown": "# x"})
    assert chat.status_code == 401 and docx.status_code == 401 and pdf.status_code == 401


def test_kit_requires_a_job():
    analysis = client.post("/api/analyze", files={"resume": RESUME}).json()
    r = client.post("/api/apply-kit", json={"resume_text": "x", "job": {"description": ""}, "analysis": analysis["analysis"]})
    assert r.status_code == 400


def test_docx_export():
    r = client.post("/api/export/docx", json={"markdown": "# Jane\n## Experience\n- Did **things**", "filename": "jane"})
    assert r.status_code == 200 and r.content[:2] == b"PK"  # .docx is a zip


def test_pdf_export():
    md = demo.DEMO_KIT["tailored_resume_markdown"] + "\n- Zoë Łukasz: 2021--2023 __x__ **bold** — done"
    r = client.post("/api/export/pdf", json={"markdown": md, "filename": "jane doe"})
    assert r.status_code == 200 and r.content[:5] == b"%PDF-"
    assert r.headers["content-type"] == "application/pdf"
    assert 'filename="jane_doe.pdf"' in r.headers["content-disposition"]
