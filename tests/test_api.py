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


def _resume_file(name: str, extra: str = ""):
    return ("resumes", (name, (demo.DEMO_RESUME + extra).encode(), "text/plain"))


def test_compare_ranks_resumes_for_the_same_job():
    weak = "\n".join(l for l in demo.DEMO_RESUME.splitlines() if "Figma" not in l and "research" not in l.lower())
    r = client.post("/api/compare", data={"job_text": "Senior Product Designer at Acme Cloud"},
                    files=[_resume_file("strong.txt"), ("resumes", ("weak.txt", weak.encode(), "text/plain"))])
    assert r.status_code == 200, r.text
    c = r.json()["comparison"]
    assert [e["name"] for e in c["entries"]] == ["strong.txt", "weak.txt"]
    assert c["entries"][0]["overall_score"] > c["entries"][1]["overall_score"] and not c["tie"]
    assert c["verdict"].startswith("strong.txt is the stronger fit")
    assert c["rows"] and all(len(row["statuses"]) == 2 for row in c["rows"])


def test_identical_resumes_are_a_tie_and_compare_needs_two(monkeypatch):
    c = client.post("/api/compare", data={"job_text": "Senior Product Designer"},
                    files=[_resume_file("a.txt"), _resume_file("b.txt")]).json()["comparison"]
    assert c["tie"] and "even match" in c["verdict"]
    assert client.post("/api/compare", data={"job_text": "x"}, files=[_resume_file("a.txt")]).status_code == 400


def test_compare_is_a_paid_feature(access_code):
    r = client.post("/api/compare", data={"job_text": "x"}, files=[_resume_file("a.txt"), _resume_file("b.txt")])
    assert r.status_code == 401


def test_outcomes_and_score_trend_per_job(admin, monkeypatch, tmp_path):
    from app import cache
    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path)
    admin.post("/api/admin/login", json={"username": "owner", "password": "test-pass"})
    job = {"job_text": "Senior Product Designer at Acme Cloud"}
    admin.post("/api/analyze", files={"resume": ("v1.txt", demo.DEMO_RESUME.encode(), "text/plain")}, data=job)
    admin.post("/api/analyze", files={"resume": ("v2.txt", (demo.DEMO_RESUME + "\nAccessibility (WCAG 2.2 AA) audits").encode(), "text/plain")}, data=job)
    items = admin.get("/api/history").json()["items"]
    newest = items[0]
    assert newest["resume_name"] == "v2.txt" and len(newest["trend"]) == 2
    assert admin.post(f"/api/history/{newest['key']}/outcome", json={"outcome": "interview"}).status_code == 200
    assert admin.post(f"/api/history/{newest['key']}/outcome", json={"outcome": "maybe"}).status_code == 400
    again = admin.get("/api/history").json()["items"]
    assert again[0]["outcome"] == "interview" and again[0]["key"] == newest["key"]  # last-used order unchanged


def test_job_suggestions_are_sorted_by_fit():
    from app import jobs
    a = jobs.Posting(title="Designer", url="https://x/1", site="indeed", description="Sketch and Miro")
    b = jobs.Posting(title="Product Designer", url="https://x/2", site="indeed", description="Figma, user research, usability testing")
    ranked = jobs.rank_by_fit([a, b], ["Figma", "user research", "usability testing", "Sketch"])
    assert [p.url for p in ranked] == ["https://x/2", "https://x/1"] and ranked[0].fit == ["Figma", "user research", "usability testing"]


def test_pdf_export_is_rechecked_for_pages_and_readable_text():
    md = "# Jane Doe\njane@example.com\n## Experience\n- Built the Figma design system used by 30 engineers\n- Led usability testing"
    r = client.post("/api/export/pdf", json={"markdown": md, "filename": "Jane Doe"})
    assert r.status_code == 200 and r.headers["x-pages"] == "1" and int(r.headers["x-text-readable"]) >= 90


def test_new_figures_guard():
    from app import ai
    assert ai.new_figures("Grew signups 40% across [N] teams, saving $2M", "Grew signups 40% across teams") == ["2"]


def test_job_match_carries_assessment_and_read_date():
    body = client.post("/api/analyze", files={"resume": RESUME}, data={"job_text": "Senior Product Designer at Acme Cloud"}).json()
    a = body["analysis"]
    assert a["assessment"]["decision"] == "provisional"  # one-line posting: too thin for a verdict
    assert body["job"]["fetched_at"] and body["job"]["sources"] == ["your paste"]


def test_health_is_admin_only_and_never_returns_keys(admin, monkeypatch):
    assert admin.get("/api/admin/health").status_code == 401
    admin.post("/api/admin/login", json={"username": "owner", "password": "test-pass"})
    monkeypatch.setenv("BRIGHTDATA_API_KEY", "")
    monkeypatch.setenv("APIFY_API_TOKEN", "")
    h = admin.get("/api/admin/health?refresh=1").json()
    assert set(h["services"]) == {"AI model", "Supabase", "Bright Data", "Apify", "JobSpy"}
    assert h["services"]["Bright Data"]["status"] == "off" and h["services"]["AI model"]["status"] == "off"  # demo, no keys
    assert "test-pass" not in str(h)
