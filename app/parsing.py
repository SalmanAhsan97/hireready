"""Resume text extraction and job-posting fetch (LinkedIn via Bright Data, any job page as fallback)."""
import asyncio
import io
import logging
import os
import re

import httpx
from bs4 import BeautifulSoup
from docx import Document
from pypdf import PdfReader

from . import cache
from .models import Job, LinkedInProfile, ProfileExperience

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)


log = logging.getLogger("hireready.fetch")


class JobFetchError(Exception):
    pass


def extract_resume_text(filename: str, data: bytes) -> str:
    name = filename.lower()
    links: list[str] = []
    if name.endswith(".pdf"):
        reader = PdfReader(io.BytesIO(data))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
        # "LinkedIn" / "Portfolio" are often clickable words: the URL lives in the link, not the text
        for page in reader.pages:
            for annot in page.get("/Annots") or []:
                uri = (annot.get_object().get("/A") or {}).get("/URI")
                if isinstance(uri, str) and uri.startswith("http") and uri not in links and uri not in text:
                    links.append(uri)
    elif name.endswith(".docx"):
        doc = Document(io.BytesIO(data))
        parts = [p.text for p in doc.paragraphs]
        for table in doc.tables:
            for row in table.rows:
                parts.append(" | ".join(cell.text for cell in row.cells))
        text = "\n".join(parts)
    elif name.endswith((".txt", ".md")):
        text = data.decode("utf-8", errors="ignore")
    else:
        raise ValueError("Upload a PDF, DOCX or TXT resume.")
    text = reflow(text)
    if links:
        text += "\nLinks: " + " ".join(links)
    if len(text) < 200:
        raise ValueError(
            "Couldn't read enough text from that resume. If it's a scanned PDF, upload a DOCX or text version."
        )
    return text


BULLET = "•▪◦●‣–-*"
LABEL = re.compile(r"^[A-Z][\w &/]{1,30}:\s")  # "Certifications: ...", "Tools: ..."
HEADING = re.compile(r"^[A-Z0-9 &/,'’()-]{3,}$")  # ALL-CAPS section headings


def reflow(text: str) -> str:
    """Undo PDF line-wrapping: a lone bullet glyph joins the line after it, and a wrapped bullet or
    paragraph line joins the line it continues. Headings, new bullets and sentence ends break lines."""
    out: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            if out and out[-1]:
                out.append("")
            continue
        prev = out[-1] if out else ""
        if prev in tuple(BULLET):  # glyph alone on the previous line
            out[-1] = f"{prev} {line}"
            continue
        starts_new = line[0] in BULLET or bool(HEADING.match(line)) or not prev or bool(LABEL.match(line))
        structured = " | " in prev or " • " in prev  # contact / role / education lines never wrap into prose
        continues = prev and not structured and prev[-1] not in ".!?:" and (prev[0] in BULLET or len(prev) >= 70)
        if not starts_new and (continues or (prev and prev[0] in BULLET and line[0].islower())):
            out[-1] = f"{prev} {line}"
        else:
            out.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


def linkedin_job_id(url: str) -> str | None:
    for pattern in (r"currentJobId=(\d+)", r"/jobs/view/(?:[^/?]*-)?(\d{6,})", r"jobPosting/(\d+)"):
        m = re.search(pattern, url)
        if m:
            return m.group(1)
    return None


def _clean(el) -> str:
    return el.get_text(" ", strip=True) if el else ""


POSTING_TTL_S = float(os.getenv("HIREREADY_POSTING_TTL_HOURS", "168")) * 3600


async def fetch_job(url: str, premium: bool = True) -> Job:
    """premium=False (free previews) never spends Bright Data/Apify credit: cache or the public page only."""
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        raise JobFetchError("That doesn't look like a URL.")
    job_id = linkedin_job_id(url) if "linkedin.com" in url else None
    if job_id:
        # Same posting text -> same extracted requirements -> same score, so reuse a recent fetch.
        cached = await cache.get("postings", job_id, Job, max_age_s=POSTING_TTL_S)
        if cached:
            return cached
        job = await (fetch_linkedin(job_id) if premium else fetch_linkedin_public(job_id))
        # Don't pin a degraded fallback: if a provider is configured but failed, refetch next time.
        if not linkedin_sources() or job.sources != ["LinkedIn public page"]:
            await cache.put("postings", job_id, job)
        return job
    if "linkedin.com" in url:
        raise JobFetchError("Couldn't find a job ID in that LinkedIn link. Use the link from the job's own page.")
    async with httpx.AsyncClient(headers={"User-Agent": UA}, follow_redirects=True, timeout=20) as http:
        resp = await http.get(url)
    if resp.status_code != 200:
        raise JobFetchError(f"The job page returned HTTP {resp.status_code}. Paste the description instead.")
    return _parse_generic(resp.text, url)


def linkedin_sources() -> list[str]:
    return [name for name, env in (("Bright Data", "BRIGHTDATA_API_KEY"), ("Apify", "APIFY_API_TOKEN")) if os.getenv(env)]


async def fetch_linkedin(job_id: str) -> Job:
    """Scrape with every configured provider at once and merge, so one provider's gaps are filled by the other.
    Falls back to LinkedIn's public guest endpoint only when no provider returned the posting."""
    scrapers = {"Bright Data": fetch_linkedin_brightdata, "Apify": fetch_linkedin_apify}
    names = linkedin_sources()
    results = await asyncio.gather(*(scrapers[n](job_id) for n in names), return_exceptions=True)
    for name, r in zip(names, results):
        if not isinstance(r, Job):
            log.warning("%s failed for LinkedIn job %s: %r", name, job_id, r)
    jobs = [r for r in results if isinstance(r, Job)]
    if jobs:
        return merge_jobs(jobs)

    try:
        return await fetch_linkedin_public(job_id)
    except JobFetchError:
        errors = [str(r) for r in results if isinstance(r, JobFetchError)]
        if errors:  # a provider's own error (e.g. a bad key) is more useful than the generic one
            raise JobFetchError(" ".join(errors))
        raise


async def fetch_linkedin_public(job_id: str) -> Job:
    async with httpx.AsyncClient(headers={"User-Agent": UA}, follow_redirects=True, timeout=20) as http:
        try:
            resp = await http.get(f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}")
        except httpx.HTTPError:
            resp = None
    job = _parse_linkedin(resp.text, f"https://www.linkedin.com/jobs/view/{job_id}/") if resp and resp.status_code == 200 else None
    if not job:
        raise JobFetchError(
            "LinkedIn didn't return this posting (it may be closed, or LinkedIn is rate-limiting). "
            "Paste the job description below instead."
        )
    job.sources = ["LinkedIn public page"]
    return job


def merge_jobs(jobs: list[Job]) -> Job:
    """Fullest description wins; other fields take the first non-empty value; criteria are unioned by label."""
    if len(jobs) == 1:
        return jobs[0]
    criteria, seen = [], set()
    for j in jobs:
        for c in j.criteria:
            label = c.split(":", 1)[0].strip().lower()
            if label not in seen:
                seen.add(label)
                criteria.append(c)
    pick = lambda field: next((getattr(j, field) for j in jobs if getattr(j, field)), "")
    return Job(
        url=pick("url"),
        title=pick("title"),
        company=pick("company"),
        location=pick("location"),
        criteria=criteria,
        description=max((j.description for j in jobs), key=len),
        sources=[s for j in jobs for s in j.sources],
    )


def _html_to_text(desc: str) -> str:
    """Keep every piece of text (not just text inside <p>/<li>), with line breaks where the HTML had them."""
    if "<" not in desc:
        return desc.strip()
    soup = BeautifulSoup(desc, "html.parser")
    for el in soup.find_all(["button", "script", "style"]):
        el.decompose()
    for br in soup.find_all("br"):
        br.replace_with("\n")
    for li in soup.find_all("li"):
        li.insert(0, "• ")
    for block in soup.find_all(["p", "div", "section", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6"]):
        block.append("\n")
    lines = [re.sub(r"[ \t\xa0]+", " ", line).strip() for line in soup.get_text().splitlines()]
    text = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


NOT_STATED = {"", "none", "null", "not applicable", "n/a"}


APIFY_API = "https://api.apify.com/v2"
APIFY_LINKEDIN_ACTOR = os.getenv("APIFY_LINKEDIN_ACTOR", "bebity~linkedin-jobs-scraper")


async def fetch_linkedin_apify(job_id: str) -> Job:
    """Scrape a LinkedIn job through an Apify actor (default: bebity/linkedin-jobs-scraper, which takes job URLs)."""
    url = f"https://www.linkedin.com/jobs/view/{job_id}/"
    unavailable = JobFetchError("Apify couldn't retrieve this posting. Paste the job description below instead.")
    async with httpx.AsyncClient(timeout=150) as http:
        try:
            resp = await http.post(
                f"{APIFY_API}/acts/{APIFY_LINKEDIN_ACTOR}/run-sync-get-dataset-items",
                params={"timeout": 120, "format": "json"},
                headers={"Authorization": f"Bearer {os.environ['APIFY_API_TOKEN']}"},
                json={"startUrls": [{"url": url}], "maxRowsPerUrl": 1},
            )
            if resp.status_code in (401, 403):
                raise JobFetchError("Apify rejected the API token. Check APIFY_API_TOKEN.")
            if resp.status_code not in (200, 201):
                raise unavailable
            records = resp.json()
        except httpx.ConnectError:
            raise JobFetchError("Couldn't reach Apify (network or DNS problem). Paste the job description instead.")
        except (httpx.HTTPError, ValueError):
            raise unavailable
    if isinstance(records, dict):
        records = [records]
    records = [r for r in records if isinstance(r, dict) and not r.get("error")]
    rec = next((r for r in records if job_id in f"{r.get('id', '')} {r.get('jobUrl', '')}"), None)
    if not rec:
        raise unavailable
    return _job_from_apify(rec, url)


def _job_from_apify(rec: dict, url: str) -> Job:
    desc = _html_to_text(_first(rec, "descriptionHtml", "description", "descriptionText"))
    if len(desc) < 100:
        raise JobFetchError("Apify returned this posting without a description. Paste it instead.")
    criteria = [
        f"{label}: {rec[key]}"
        for label, key in [
            ("Seniority level", "experienceLevel"),
            ("Employment type", "contractType"),
            ("Workplace type", "workType"),
            ("Job function", "jobFunction"),
            ("Industries", "sector"),
            ("Salary", "salary"),
            ("Benefits", "benefits"),
        ]
        if str(rec.get(key) or "").strip().lower() not in NOT_STATED
    ]
    return Job(
        url=url,
        title=_first(rec, "title"),
        company=_first(rec, "companyName"),
        location=_first(rec, "location"),
        criteria=criteria,
        description=desc,
        sources=["Apify"],
    )


BD_API = "https://api.brightdata.com/datasets/v3"
BD_LINKEDIN_JOBS = os.getenv("BRIGHTDATA_LINKEDIN_JOBS_DATASET", "gd_lpfll7v5hcqtkxl6l")


async def fetch_linkedin_brightdata(job_id: str) -> Job:
    """Scrape a LinkedIn job through Bright Data's Scraper API, so no LinkedIn account or our own IP is used."""
    url = f"https://www.linkedin.com/jobs/view/{job_id}/"
    headers = {"Authorization": f"Bearer {os.environ['BRIGHTDATA_API_KEY']}"}
    unavailable = JobFetchError("Bright Data couldn't retrieve this posting. Paste the job description below instead.")
    async with httpx.AsyncClient(headers=headers, timeout=90) as http:
        try:
            resp = await http.post(
                f"{BD_API}/scrape",
                params={"dataset_id": BD_LINKEDIN_JOBS, "format": "json"},
                json=[{"url": url}],
            )
            if resp.status_code in (401, 403):
                raise JobFetchError("Bright Data rejected the API key. Check BRIGHTDATA_API_KEY.")
            if resp.status_code == 202:  # took longer than the sync window; poll the snapshot
                records = await _bd_wait_snapshot(http, resp.json()["snapshot_id"])
            elif resp.status_code == 200:
                records = resp.json()
            else:
                raise unavailable
        except httpx.ConnectError:
            raise JobFetchError("Couldn't reach Bright Data (network or DNS problem). Paste the job description instead.")
        except (httpx.HTTPError, ValueError, KeyError):
            raise unavailable
    if isinstance(records, dict):
        records = [records]
    rec = next((r for r in records if isinstance(r, dict) and not r.get("error")), None)
    if not rec:
        raise unavailable
    return _job_from_brightdata(rec, url)


async def _bd_wait_snapshot(http: httpx.AsyncClient, snapshot_id: str, timeout_s: int = 120):
    for _ in range(timeout_s // 5):
        await asyncio.sleep(5)
        prog = (await http.get(f"{BD_API}/progress/{snapshot_id}")).json()
        if prog.get("status") == "ready":
            return (await http.get(f"{BD_API}/snapshot/{snapshot_id}", params={"format": "json"})).json()
        if prog.get("status") == "failed":
            break
    raise JobFetchError("Bright Data took too long on this posting. Paste the job description instead.")


def _first(rec: dict, *keys) -> str:
    for k in keys:
        v = rec.get(k)
        if isinstance(v, dict):
            v = v.get("name") or v.get("text")
        if v:
            return str(v).strip()
    return ""


def _job_from_brightdata(rec: dict, url: str) -> Job:
    desc = _html_to_text(_first(rec, "job_description_formatted", "job_description", "description", "job_summary"))
    if len(desc) < 100:
        raise JobFetchError("Bright Data returned this posting without a description. Paste it instead.")
    criteria = []
    for label, keys in [
        ("Seniority level", ("job_seniority_level", "seniority_level")),
        ("Employment type", ("job_employment_type", "employment_type")),
        ("Job function", ("job_function",)),
        ("Industries", ("job_industries", "industries")),
        ("Salary", ("base_salary", "salary")),
    ]:
        v = rec.get(keys[0]) or (rec.get(keys[1]) if len(keys) > 1 else None)
        if isinstance(v, dict):
            v = " ".join(str(x) for x in v.values() if x)
        if str(v or "").strip().lower() not in NOT_STATED:
            criteria.append(f"{label}: {v}")
    return Job(
        url=_first(rec, "url") or url,
        title=_first(rec, "job_title", "title"),
        company=_first(rec, "company_name", "company"),
        location=_first(rec, "job_location", "location"),
        criteria=criteria,
        description=desc,
        sources=["Bright Data"],
    )


def _parse_linkedin(html: str, url: str) -> Job | None:
    soup = BeautifulSoup(html, "html.parser")
    desc_el = soup.select_one(".show-more-less-html__markup") or soup.select_one(".description__text")
    if not desc_el:
        return None
    for br in desc_el.find_all("br"):
        br.replace_with("\n")
    for li in desc_el.find_all("li"):
        li.insert_before("\n• ")
    description = re.sub(r"\n\s*\n+", "\n\n", desc_el.get_text()).strip()
    criteria = []
    for item in soup.select(".description__job-criteria-item"):
        k, v = _clean(item.select_one("h3")), _clean(item.select_one("span"))
        if k and v:
            criteria.append(f"{k}: {v}")
    return Job(
        url=url,
        title=_clean(soup.select_one(".top-card-layout__title, .topcard__title")),
        company=_clean(soup.select_one(".topcard__org-name-link, .topcard__flavor a")),
        location=_clean(soup.select_one(".topcard__flavor--bullet")),
        criteria=criteria,
        description=description,
    )


def _parse_generic(html: str, url: str) -> Job:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer", "noscript", "svg"]):
        tag.decompose()
    title = _clean(soup.find("h1")) or _clean(soup.find("title"))
    main = soup.find("main") or soup.find("article") or soup.body or soup
    text = re.sub(r"\n\s*\n+", "\n\n", main.get_text("\n", strip=True))
    if len(text) < 300:
        raise JobFetchError("That page didn't contain a readable job description. Paste it instead.")
    return Job(url=url, title=title, description=text[:20000])


# ---- LinkedIn profiles (Apify) ----

APIFY_PROFILE_ACTOR = os.getenv("APIFY_PROFILE_ACTOR", "harvestapi~linkedin-profile-scraper")


def linkedin_profile_id(url: str) -> str | None:
    m = re.search(r"linkedin\.com/in/([^/?#]+)", url)
    return m.group(1).lower() if m else None


async def fetch_linkedin_profile(url: str) -> LinkedInProfile:
    pid = linkedin_profile_id(url.strip())
    if not pid:
        raise JobFetchError("Use your profile link, like https://www.linkedin.com/in/your-name/")
    cached = await cache.get("li-profiles", pid, LinkedInProfile, max_age_s=POSTING_TTL_S)
    if cached:
        return cached
    if not os.getenv("APIFY_API_TOKEN"):
        raise JobFetchError("Profile links aren't available right now. Upload your LinkedIn PDF instead (Profile → More → Save to PDF).")
    unavailable = JobFetchError("Couldn't read that LinkedIn profile. Check the link is public, or upload your LinkedIn PDF instead.")
    async with httpx.AsyncClient(timeout=150) as http:
        try:
            resp = await http.post(
                f"{APIFY_API}/acts/{APIFY_PROFILE_ACTOR}/run-sync-get-dataset-items",
                params={"timeout": 120},
                headers={"Authorization": f"Bearer {os.environ['APIFY_API_TOKEN']}"},
                json={"urls": [f"https://www.linkedin.com/in/{pid}/"]},
            )
            records = resp.json() if resp.status_code in (200, 201) else []
        except (httpx.HTTPError, ValueError):
            raise unavailable
    rec = next((r for r in (records if isinstance(records, list) else [records]) if isinstance(r, dict) and r.get("headline") is not None), None)
    if not rec:
        raise unavailable
    profile = _profile_from_apify(rec, pid)
    await cache.put("li-profiles", pid, profile)
    return profile


def _date_text(d) -> str:
    return (d or {}).get("text", "") if isinstance(d, dict) else str(d or "")


def _profile_from_apify(rec: dict, pid: str) -> LinkedInProfile:
    totals = rec.get("sectionTotals") or {}
    featured = rec.get("featured") or {}
    return LinkedInProfile(
        name=" ".join(x for x in [rec.get("firstName"), rec.get("lastName")] if x),
        headline=rec.get("headline") or "",
        about=rec.get("about") or "",
        location=((rec.get("location") or {}).get("linkedinText") or ""),
        experiences=[
            ProfileExperience(
                title=e.get("position") or "",
                company=e.get("companyName") or "",
                description=e.get("description") or "",
                start=_date_text(e.get("startDate")),
                end=_date_text(e.get("endDate")) or ("Present" if e.get("startDate") else ""),
            )
            for e in rec.get("experience") or []
        ],
        skills=[s.get("name", "") for s in rec.get("skills") or [] if isinstance(s, dict)] + list(rec.get("topSkills") or []),
        skills_total=totals.get("skills", len(rec.get("skills") or [])),
        certifications=totals.get("certifications", len(rec.get("certifications") or [])),
        recommendations=totals.get("receivedRecommendations", len(rec.get("receivedRecommendations") or [])),
        education=totals.get("education", len(rec.get("education") or [])),
        connections=rec.get("connectionsCount"),
        has_photo=bool(rec.get("photo") or (rec.get("profilePicture") or {}).get("url")),
        has_banner=bool((rec.get("coverPicture") or {}).get("url")),
        has_featured=bool(featured.get("slides") or featured.get("images") or featured.get("link")),
        custom_url=not re.search(r"-[0-9a-f]{6,}$|\d{5,}$", pid),  # default URLs end in a random suffix
        source="LinkedIn (via Apify)",
        open_to_work=rec.get("openToWork"),
        # the default scrape has no contact info; only judge it when the record actually carries the fields
        has_contact=bool(rec.get("emails") or rec.get("websites")) if ("emails" in rec or "websites" in rec) else None,
    )
