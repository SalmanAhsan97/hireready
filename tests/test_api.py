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


@pytest.fixture
def admin(monkeypatch, access_code):
    monkeypatch.setattr(main, "ADMIN_USER", "owner")
    monkeypatch.setattr(main, "ADMIN_PASSWORD", "test-pass")
    main._login_failures.clear()
    return TestClient(main.app)


def test_admin_sign_in_unlocks_the_full_version(admin):
    assert admin.post("/api/admin/login", json={"username": "owner", "password": "nope"}).status_code == 401
    assert admin.post("/api/admin/login", json={"username": "Owner", "password": "test-pass"}).status_code == 200
    assert admin.get("/api/admin").json() == {"admin": True}
    body = admin.post("/api/analyze", files={"resume": RESUME}, data={"job_text": "Senior Product Designer"}).json()
    assert body["preview"] is False and body["analysis"]["requirements"]
    admin.post("/api/admin/logout")
    assert admin.get("/api/admin").json() == {"admin": False}
    assert admin.post("/api/analyze", files={"resume": RESUME}, data={"job_text": "Senior Product Designer"}).json()["preview"] is True


def test_admin_cookie_is_httponly_and_forged_cookies_fail(admin):
    r = admin.post("/api/admin/login", json={"username": "owner", "password": "test-pass"})
    assert "httponly" in r.headers["set-cookie"].lower()
    forged = TestClient(main.app, cookies={main.ADMIN_COOKIE: "x" * 64})
    assert forged.get("/api/admin").json() == {"admin": False}


def test_admin_sign_in_is_off_without_credentials(monkeypatch):
    monkeypatch.setattr(main, "ADMIN_USER", "")
    monkeypatch.setattr(main, "ADMIN_PASSWORD", "")
    assert client.post("/api/admin/login", json={"username": "", "password": ""}).status_code == 404
    assert client.get("/api/status").json()["admin_login"] is False


def test_admin_runs_are_saved_and_can_be_previewed(admin, monkeypatch, tmp_path):
    from app import cache
    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path)
    assert admin.get("/api/history").status_code == 403  # not signed in yet
    admin.post("/api/admin/login", json={"username": "owner", "password": "test-pass"})
    client.post("/api/analyze", files={"resume": RESUME})  # a visitor's run is never saved
    admin.post("/api/analyze", files={"resume": ("my-cv.txt", demo.DEMO_RESUME.encode(), "text/plain")},
               data={"job_text": "Senior Product Designer at Acme Cloud"})
    items = admin.get("/api/history").json()["items"]
    assert [i["resume_name"] for i in items] == ["my-cv.txt"] and items[0]["last_used"]
    run = admin.get(f"/api/history/{items[0]['key']}").json()
    assert run["resume_text"].startswith(demo.DEMO_RESUME[:40]) and run["analysis"]["mode"] == "job"
    assert admin.get("/api/history/not-a-key").status_code == 404
