"""Job search with JobSpy mocked: no network."""
import sys
import types

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app import jobs, main

client = TestClient(main.app)

ROWS = [
    {"site": "indeed", "title": "Product Designer", "company": "Acme", "location": "Toronto, ON, CA",
     "date_posted": "2026-10-10", "job_url": "https://ca.indeed.com/viewjob?jk=1", "is_remote": False,
     "description": "Design things in Figma."},
    {"site": "indeed", "title": "Senior Product Designer", "company": float("nan"), "location": None,
     "date_posted": "2026-10-14", "job_url": "https://ca.indeed.com/viewjob?jk=2", "is_remote": True,
     "description": float("nan")},
    {"site": "indeed", "title": "Duplicate", "company": "Acme", "location": "", "date_posted": "2026-10-01",
     "job_url": "https://ca.indeed.com/viewjob?jk=1", "is_remote": False, "description": ""},
    {"site": "indeed", "title": "Bad link", "company": "X", "location": "", "date_posted": "2026-10-12",
     "job_url": "javascript:alert(1)", "is_remote": False, "description": ""},
]


@pytest.fixture
def fake_jobspy(monkeypatch):
    calls = []

    def scrape_jobs(**kw):
        calls.append(kw)
        return pd.DataFrame(ROWS)

    monkeypatch.setitem(sys.modules, "jobspy", types.SimpleNamespace(scrape_jobs=scrape_jobs))
    return calls


def test_search_normalises_rows(fake_jobspy):
    r = client.post("/api/jobs", json={"role": "Product Designer", "location": "Toronto", "country": "Canada"})
    assert r.status_code == 200
    found = r.json()["jobs"]
    assert [j["url"][-1] for j in found] == ["2", "1"]  # newest first, duplicate and non-http link dropped
    assert found[0]["company"] == "" and found[0]["description"] == "" and found[0]["remote"] is True
    assert found[1]["description"] == "Design things in Figma."
    kw = fake_jobspy[0]
    assert kw["search_term"] == "Product Designer" and kw["country_indeed"] == "Canada" and kw["site_name"] == ["indeed"]


def test_search_is_cached(fake_jobspy):
    body = {"role": "Data Analyst", "location": "Austin", "country": "USA"}
    client.post("/api/jobs", json=body)
    client.post("/api/jobs", json=body)
    assert len(fake_jobspy) == 1


def test_empty_result_is_not_cached(monkeypatch):
    calls = []
    monkeypatch.setitem(sys.modules, "jobspy", types.SimpleNamespace(
        scrape_jobs=lambda **kw: calls.append(kw) or pd.DataFrame()))
    body = {"role": "Lighthouse Keeper", "country": "UK"}
    assert client.post("/api/jobs", json=body).json()["jobs"] == []
    client.post("/api/jobs", json=body)
    assert len(calls) == 2


def test_scrape_failure_is_a_clean_error(monkeypatch):
    def boom(**kw):
        raise ConnectionError("blocked")
    monkeypatch.setitem(sys.modules, "jobspy", types.SimpleNamespace(scrape_jobs=boom))
    r = client.post("/api/jobs", json={"role": "Nurse", "country": "USA"})
    assert r.status_code == 502 and "job boards" in r.json()["detail"]


def test_needs_role_and_known_country(fake_jobspy):
    assert client.post("/api/jobs", json={"role": " "}).status_code == 400
    assert client.post("/api/jobs", json={"role": "Nurse", "country": "Atlantis"}).status_code == 400
    assert not fake_jobspy


def test_needs_access_code(monkeypatch, fake_jobspy):
    monkeypatch.setattr(main, "ACCESS_CODE", "secret-code")
    assert client.post("/api/jobs", json={"role": "Nurse"}).status_code == 401
    ok = client.post("/api/jobs", json={"role": "Nurse"}, headers={"X-Access-Code": "secret-code"})
    assert ok.status_code == 200


def test_daily_limit(monkeypatch, fake_jobspy):
    monkeypatch.setattr(main, "JOBS_LIMIT", 1)
    monkeypatch.setattr(main, "_client_id", lambda request: "jobs-limit-test")
    assert client.post("/api/jobs", json={"role": "Chef"}).status_code == 200
    assert client.post("/api/jobs", json={"role": "Chef"}).status_code == 429


def test_status_lists_countries():
    assert "Pakistan" in client.get("/api/status").json()["job_countries"]
