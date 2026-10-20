"""Admin-only importer for the optional data layer: ChatGPT data exports and handover notes.

    python -m app.importer <file> [<file> ...] [--project TEXT] [--dry-run]

Inputs: a ChatGPT export (.zip holding conversations.json, or conversations.json itself), handover Markdown
(.md/.txt), or an already structured .json matching models.ImportBatch. There is no ChatGPT API for project
chats, so imports are file-based. --project keeps only conversations whose title or project id contains TEXT.

The model only turns messy text into structures (ImportBatch); code validates, cleans and stores:
  postings     -> screenproof_postings (no candidate data: contact details are stripped)
  applications -> screenproof_outcomes, scored with the normal scorer when the posting and resume text are
                  both there; only the score and outcome are kept, never the texts
  vault        -> screenproof_vault (owner only)
Idempotent: a file imported before (same content hash) is skipped, and every row is keyed by its own hash.
"""
import argparse
import asyncio
import hashlib
import io
import json
import logging
import re
import sys
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from pydantic import ValidationError

load_dotenv(Path(__file__).resolve().parent.parent / ".env")  # as app.main does, for the CLI

from . import ai, cache, jobs
from .models import ImportApplication, ImportBatch, ImportPosting, ImportVaultItem, Job
from .scoring import _norm

log = logging.getLogger("screenproof.importer")

CHUNK_CHARS = 12000
MAX_CHUNKS = 200  # cost guard: about 2.4M characters of chat per import
MAX_DESCRIPTION = jobs.MAX_DESCRIPTION
MIN_TEXT = 400  # a posting or resume shorter than this is a mention, not the document
RELEVANT = re.compile(r"resume|résumé|\bcv\b|job (?:posting|description|ad)|\bjd\b|applied|application|interview|"
                      r"recruiter|cover letter|hiring|callback|rejected|offer|linkedin|roas|achievement", re.I)

IMPORT_INSTRUCTIONS = """This is part of the owner's own job-search notes (a chat export or a handover document). \
Read it into three lists. Copy text verbatim; never invent anything. Leave a list empty when the text has none.

postings: every real job posting quoted in the text. title, company, location, market (country), posted_on \
(YYYY-MM-DD or YYYY-MM if stated, else ""), description (the posting text verbatim, only the employer's words, \
never the candidate's resume or messages) and terms (skills and tools the posting asks for, as written).

applications: every job the owner applied to. role, company, applied_on, outcome (applied = no answer yet, \
interview, offer, rejected, no_reply = ghosted, unknown), job_text (the full posting verbatim if it is in the \
text, else "") and resume_text (the exact resume version sent, verbatim, if it is in the text, else "").

vault: facts about the owner's own career, as rules for future resumes. kind: fact (a verified fact), figure \
(an achievement number), removed (a claim that must not be used), corrected (a claim replaced by \
better wording; put the new wording in correction), limit (something to state only a limited number of times; \
set max_mentions). status: verified, unverified or removed. figure: the number itself ("4x", "35%") when there \
is one. applies_to: the role variant the rule is scoped to (e.g. "Social Media Manager"), "" when it applies to \
every resume."""


class ImportFailed(Exception):
    pass


# ---- Reading files ----

def _message_text(msg: dict) -> str:
    content = msg.get("content") or {}
    parts = content.get("parts") or []
    if content.get("content_type") == "text" or parts:
        return "\n".join(p for p in parts if isinstance(p, str)).strip()
    return str(content.get("text") or "").strip()


def conversations_text(conversations: list, project: str = "") -> list[str]:
    """One text per conversation, messages in time order, kept only if it touches the job search."""
    out = []
    project = project.lower().strip()
    for conv in conversations if isinstance(conversations, list) else []:
        if not isinstance(conv, dict):
            continue
        title = str(conv.get("title") or "")
        ids = " ".join(str(conv.get(k) or "") for k in ("gizmo_id", "conversation_template_id", "project_id"))
        if project and project not in f"{title} {ids}".lower():
            continue
        msgs = []
        for node in (conv.get("mapping") or {}).values():
            msg = (node or {}).get("message") or {}
            role = (msg.get("author") or {}).get("role")
            text = _message_text(msg)
            if role in ("user", "assistant") and text:
                msgs.append((msg.get("create_time") or 0, role, text))
        msgs.sort(key=lambda m: m[0])
        text = f"# {title}\n\n" + "\n\n".join(f"[{role}] {text}" for _, role, text in msgs)
        if msgs and RELEVANT.search(text):
            out.append(text)
    return out


def read_file(name: str, data: bytes, project: str = "") -> tuple[str, list[str] | ImportBatch]:
    """(kind, texts to read) or (kind, an already structured batch)."""
    low = name.lower()
    if low.endswith(".zip"):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                member = next((n for n in z.namelist() if n.rsplit("/", 1)[-1] == "conversations.json"), None)
                if not member:
                    raise ImportFailed("That zip has no conversations.json. Use the ChatGPT data export zip.")
                conversations = json.loads(z.read(member))
        except (zipfile.BadZipFile, ValueError) as e:
            raise ImportFailed(f"Couldn't read that zip: {e}")
        return "chatgpt", conversations_text(conversations, project)
    if low.endswith(".json"):
        try:
            parsed = json.loads(data)
        except ValueError as e:
            raise ImportFailed(f"That JSON file doesn't parse: {e}")
        if isinstance(parsed, list):
            return "chatgpt", conversations_text(parsed, project)
        try:
            return "structured", ImportBatch.model_validate(parsed)
        except ValidationError as e:
            raise ImportFailed(f"That JSON isn't a ChatGPT export or an import batch: {e.error_count()} problems")
    if low.endswith((".md", ".markdown", ".txt")):
        return "markdown", [data.decode("utf-8", errors="replace")]
    raise ImportFailed("Upload a ChatGPT export (.zip or conversations.json), a Markdown handover (.md) or an import .json.")


def chunks(texts: list[str], size: int = CHUNK_CHARS) -> list[str]:
    """Split on paragraph breaks into pieces of at most `size` characters (a longer paragraph is cut)."""
    out, cur = [], ""
    for text in texts:
        for para in re.split(r"\n{2,}", text):
            while len(para) > size:
                if cur:
                    out.append(cur)
                    cur = ""
                out.append(para[:size])
                para = para[size:]
            if len(cur) + len(para) + 2 > size and cur:
                out.append(cur)
                cur = ""
            cur = f"{cur}\n\n{para}" if cur else para
        if cur:
            out.append(cur)
            cur = ""
    return out


async def parse_chunk(text: str) -> ImportBatch:
    """The model's reading of one chunk. Tests swap this out; demo mode can't read free text."""
    if ai.demo_mode():
        raise ImportFailed("Reading chat exports and notes needs an AI model, and Screenproof isn't connected to one. "
                           "A structured import .json works without one.")
    return await ai._parse(f"<notes>\n{text}\n</notes>\n\n{IMPORT_INSTRUCTIONS}", ImportBatch, effort="low")


# ---- Validation and cleaning (code, not the model) ----

EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
PHONE = re.compile(r"(?<!\w)\+?\d[\d\s().-]{8,}\d(?!\w)")
URL_PERSONAL = re.compile(r"https?://(?:www\.)?linkedin\.com/in/\S+", re.I)


def _clean(text: str, limit: int) -> str:
    text = URL_PERSONAL.sub("[profile link]", EMAIL.sub("[email]", text))
    text = PHONE.sub(lambda m: m.group(0) if not 10 <= sum(c.isdigit() for c in m.group(0)) <= 15 else "[phone]", text)
    return text.strip()[:limit]


def _date(value: str) -> str | None:
    value = (value or "").strip()
    for fmt, n in (("%Y-%m-%d", 10), ("%Y-%m", 7)):
        try:
            d = datetime.strptime(value[:n], fmt).date()
        except ValueError:
            continue
        return d.isoformat() if date(2000, 1, 1) <= d <= date.today() else None
    return None


def _market(value: str) -> str:
    """A country from jobs.COUNTRIES when the text names one, else "" (guess_country would say USA)."""
    low = value.lower()
    named = any(c.lower() in low for c in jobs.COUNTRIES) or any(
        k in low for k in ("united states", "united kingdom", "england", "emirates", "dubai"))
    return ai.guess_country(value) if named else ""


def _hash(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()


def posting_row(p: ImportPosting, source: str, import_hash: str, now: str) -> dict | None:
    title = " ".join(p.title.split())[:120]
    description = _clean(p.description, MAX_DESCRIPTION)
    terms = list(dict.fromkeys(" ".join(t.split())[:60] for t in p.terms if 1 < len(t.strip()) <= 60))[:40]
    if len(title) < 3 or not (len(description) >= MIN_TEXT or terms):
        return None  # a title with neither text nor terms adds nothing to count
    company = " ".join(p.company.split())[:120]
    return {
        "content_hash": _hash("posting", _norm(title), _norm(company), _norm(description)[:3000]),
        "title": title, "title_norm": _norm(title), "company": company, "location": " ".join(p.location.split())[:120],
        "market": _market(p.market or p.location), "posted_on": _date(p.posted_on), "description": description,
        "terms": terms, "source": source, "import_hash": import_hash, "created_at": now,
    }


def application_key(a: ImportApplication) -> str:
    return _hash("outcome", _norm(a.role), _norm(a.company), _date(a.applied_on) or "",
                 hashlib.sha256(a.job_text.strip().encode()).hexdigest(), hashlib.sha256(a.resume_text.strip().encode()).hexdigest())


def scorable(a: ImportApplication) -> bool:
    return a.outcome in cache.OUTCOMES and len(a.job_text.strip()) >= MIN_TEXT and len(a.resume_text.strip()) >= MIN_TEXT


def vault_row(v: ImportVaultItem, import_hash: str, now: str) -> dict | None:
    claim = " ".join(v.claim.split())[:300]
    if len(claim) < 3:
        return None
    status = "removed" if v.kind == "removed" else v.status
    max_mentions = v.max_mentions if v.kind == "limit" and v.max_mentions is not None and 0 <= v.max_mentions <= 20 else None
    if v.kind == "limit" and max_mentions is None:
        return None
    figure, applies = " ".join(v.figure.split())[:40], " ".join(v.applies_to.split())[:120]
    return {
        "content_hash": _hash("vault", v.kind, _norm(claim), _norm(figure), _norm(applies)),
        "kind": v.kind, "claim": claim, "figure": figure, "status": status, "applies_to": applies,
        "max_mentions": max_mentions, "correction": " ".join(v.correction.split())[:300],
        "import_hash": import_hash, "created_at": now,
    }


# ---- Import ----

async def import_bytes(name: str, data: bytes, project: str = "", dry_run: bool = False, parse=None) -> dict:
    """Read, validate and store one file. Returns counts; {"skipped": True} when this file was imported before."""
    parse = parse or parse_chunk
    file_hash = hashlib.sha256(data).hexdigest()
    if not dry_run and await cache.data_has("imports", [file_hash]):
        return {"file": name, "skipped": True, "reason": "This exact file was imported before."}
    kind, content = read_file(name, data, project)
    pieces = [] if isinstance(content, ImportBatch) else chunks(content)
    if len(pieces) > MAX_CHUNKS:
        raise ImportFailed(f"That's {len(pieces)} pieces of text; the limit is {MAX_CHUNKS} per import. "
                           "Use --project to import one project's chats.")
    batches = [content] if isinstance(content, ImportBatch) else []
    for piece in pieces:
        try:
            batches.append(await parse(piece))
        except ai.AIError as e:
            raise ImportFailed(f"The model couldn't read part of the file: {e}")
    now = datetime.now(timezone.utc).isoformat()
    source = "structured" if kind == "structured" else kind
    postings = {r["content_hash"]: r for b in batches for p in b.postings if (r := posting_row(p, source, file_hash, now))}
    vault_rows = {r["content_hash"]: r for b in batches for v in b.vault if (r := vault_row(v, file_hash, now))}
    apps = {application_key(a): a for b in batches for a in b.applications}
    usable = {k: a for k, a in apps.items() if scorable(a)}
    known = await cache.data_has("outcomes", list(usable)) if not dry_run else set()
    outcome_rows = []
    for k, a in usable.items():
        if k in known:
            continue
        try:
            score = await ai.score_only(a.resume_text, Job(title=a.role, company=a.company, description=a.job_text))
        except ai.AIError as e:
            log.warning("Scoring an imported application failed: %r", e)
            continue
        outcome_rows.append({"content_hash": k, "score": score, "outcome": a.outcome, "role": " ".join(a.role.split())[:120],
                             "company": " ".join(a.company.split())[:120], "applied_on": _date(a.applied_on),
                             "source": source, "import_hash": file_hash, "created_at": now})
    counts = {"pieces_read": len(pieces), "postings_found": len(postings), "applications_found": len(apps),
              "applications_scorable": len(usable), "vault_found": len(vault_rows)}
    if dry_run:
        return {"file": name, "kind": kind, "dry_run": True, **counts}
    try:
        counts["postings_new"] = await cache.data_insert("postings", list(postings.values()))
        counts["outcomes_new"] = await cache.data_insert("outcomes", outcome_rows)
        counts["vault_new"] = await cache.data_insert("vault", list(vault_rows.values()))
        await cache.data_insert("imports", [{"content_hash": file_hash, "filename": name[:200], "kind": kind,
                                             "counts": counts, "created_at": now}])
    except cache.DataStoreError as e:
        raise ImportFailed(str(e))
    return {"file": name, "kind": kind, **counts}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.importer", description=__doc__.split("\n\n")[0])
    ap.add_argument("files", nargs="+", type=Path)
    ap.add_argument("--project", default="", help="only conversations whose title or project id contains this")
    ap.add_argument("--dry-run", action="store_true", help="read and count, store nothing")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if not cache.supabase_enabled():
        print("Note: Supabase isn't configured, so this stores to the local .cache/data folder only.", file=sys.stderr)
    status = 0
    for path in args.files:
        try:
            result = asyncio.run(import_bytes(path.name, path.read_bytes(), args.project, args.dry_run))
            print(json.dumps(result))
        except (OSError, ImportFailed) as e:
            print(f"{path.name}: {e}", file=sys.stderr)
            status = 1
    return status


if __name__ == "__main__":
    sys.exit(main())
