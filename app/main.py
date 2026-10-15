import hashlib
import hmac
import io
import os
import re
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from docx import Document  # noqa: E402
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import ai, cache
from .models import Analysis, Job
from .parsing import JobFetchError, extract_resume_text, fetch_job, linkedin_sources

STATIC = Path(__file__).resolve().parent.parent / "static"
MAX_UPLOAD = 5 * 1024 * 1024

app = FastAPI(title="HireReady")

# Public deploys: require a shared access code and cap paid work (scrapes + Claude calls) per visitor per day.
ACCESS_CODE = os.getenv("HIREREADY_ACCESS_CODE", "")
DAILY_LIMIT = int(os.getenv("HIREREADY_DAILY_LIMIT", "15"))
_SALT = os.getenv("HIREREADY_CLIENT_SALT", ACCESS_CODE or "hireready")


def require_access(request: Request):
    if ACCESS_CODE and not hmac.compare_digest(request.headers.get("x-access-code", ""), ACCESS_CODE):
        raise HTTPException(401, {"code": "access_code", "message": "Enter the access code to use HireReady."})


def _client_id(request: Request) -> str:
    ip = request.client.host if request.client else "unknown"
    return hashlib.sha256(f"{_SALT}:{ip}".encode()).hexdigest()[:24]


async def enforce_limit(request: Request, action: str):
    if DAILY_LIMIT <= 0:
        return
    used = await cache.count_and_record_usage(_client_id(request), action)
    if used > DAILY_LIMIT:
        raise HTTPException(429, f"You've reached today's limit of {DAILY_LIMIT} runs. Try again tomorrow.")


@app.get("/api/status")
def status():
    return {
        "demo": ai.demo_mode(),
        "model": ai.model_name(),
        "linkedin_sources": linkedin_sources() or ["LinkedIn public page"],
        "storage": "supabase" if cache.supabase_enabled() else "disk",
        "access_code_required": bool(ACCESS_CODE),
    }


@app.post("/api/analyze", dependencies=[Depends(require_access)])
async def analyze(
    request: Request,
    resume: UploadFile = File(...),
    job_url: str = Form(""),
    job_text: str = Form(""),
):
    data = await resume.read()
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, "Resume file is over 5 MB.")
    try:
        resume_text = extract_resume_text(resume.filename or "", data)
    except ValueError as e:
        raise HTTPException(400, str(e))

    if not (job_text.strip() or job_url.strip()):
        raise HTTPException(400, "Add a job link or paste the job description.")
    await enforce_limit(request, "analyze")

    if job_text.strip():
        job = Job(url=job_url.strip(), description=job_text.strip())
    else:
        try:
            job = await fetch_job(job_url)
        except JobFetchError as e:
            raise HTTPException(422, {"code": "job_fetch_failed", "message": str(e)})

    try:
        analysis = await ai.analyze(resume_text, job)
    except ai.AIError as e:
        raise HTTPException(502, str(e))
    await cache.save_analysis({
        "job_url": job.url,
        "job_title": analysis.job_title,
        "company": analysis.company,
        "candidate_name": analysis.candidate_name,
        "overall_score": analysis.overall_score,
        "standing": analysis.standing,
        "must_pct": analysis.coverage.must_pct if analysis.coverage else None,
        "resume_sha256": hashlib.sha256(resume_text.encode()).hexdigest(),
        "demo": ai.demo_mode(),
        "job": job.model_dump(mode="json"),
        "analysis": analysis.model_dump(mode="json"),
    })
    return {"resume_text": resume_text, "job": job, "analysis": analysis, "demo": ai.demo_mode()}


class KitRequest(BaseModel):
    resume_text: str
    job: Job
    analysis: Analysis


@app.post("/api/apply-kit", dependencies=[Depends(require_access)])
async def apply_kit(req: KitRequest, request: Request):
    await enforce_limit(request, "kit")
    try:
        kit = await ai.build_kit(req.resume_text, req.job, req.analysis)
    except ai.AIError as e:
        raise HTTPException(502, str(e))
    return {"kit": kit, "demo": ai.demo_mode()}


class DocxRequest(BaseModel):
    markdown: str
    filename: str = "resume"


def _add_runs(par, text: str):
    for i, chunk in enumerate(re.split(r"\*\*(.+?)\*\*", text)):
        if chunk:
            par.add_run(chunk).bold = i % 2 == 1


@app.post("/api/export/docx", dependencies=[Depends(require_access)])
def export_docx(req: DocxRequest):
    doc = Document()
    for line in req.markdown.splitlines():
        s = line.rstrip()
        if not s.strip():
            continue
        m = re.match(r"^(#{1,3})\s+(.*)", s)
        if m:
            doc.add_heading(m.group(2), level=len(m.group(1)) - 1 if len(m.group(1)) > 1 else 0)
        elif re.match(r"^\s*[-*•]\s+", s):
            _add_runs(doc.add_paragraph(style="List Bullet"), re.sub(r"^\s*[-*•]\s+", "", s))
        else:
            _add_runs(doc.add_paragraph(), s)
    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)
    name = re.sub(r"[^\w-]+", "_", req.filename).strip("_") or "resume"
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{name}.docx"'},
    )


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
