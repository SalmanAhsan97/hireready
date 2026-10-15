"""Model calls: extract requirements + evidence, write the narrative around a computed score, build the kit.

Uses Claude when an Anthropic key is set, otherwise Gemini (GEMINI_API_KEY), otherwise demo mode.

The score itself is computed in scoring.py. Extractions are cached on disk, so re-running the same
resume against the same job reuses the same facts and therefore gives the same score.
"""
import asyncio
import hashlib
import json
import os

import anthropic
import httpx
from pydantic import ValidationError

from . import cache, scoring
from .models import (
    Analysis,
    ApplyKit,
    Extraction,
    Job,
    JobRequirements,
    Narrative,
    ResumeEvidence,
    ResumeNarrative,
    ResumeProfile,
)

MODEL = os.getenv("HIREREADY_MODEL", "claude-opus-5-5")
GEMINI_MODEL = os.getenv("HIREREADY_GEMINI_MODEL", "gemini-flash-latest")
GEMINI_FALLBACK = os.getenv("HIREREADY_GEMINI_FALLBACK", "gemini-flash-lite-latest")
GEMINI_API = "https://generativelanguage.googleapis.com/v1beta/models"

_client: anthropic.AsyncAnthropic | None = None


class AIError(Exception):
    pass


def provider() -> str | None:
    if os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN"):
        return "anthropic"
    if os.getenv("GEMINI_API_KEY"):
        return "gemini"
    return None


def model_name() -> str:
    return GEMINI_MODEL if provider() == "gemini" else MODEL


def demo_mode() -> bool:
    return os.getenv("HIREREADY_DEMO") == "1" or provider() is None


def client() -> anthropic.AsyncAnthropic:
    global _client
    if _client is None:
        _client = anthropic.AsyncAnthropic(timeout=300)
    return _client


SYSTEM = """You are a senior technical recruiter and resume strategist who has screened thousands of \
applicants and knows how ATS filters and hiring managers read resumes.

Ground rules:
- Judge the resume strictly against THIS job's requirements, not in general.
- Never invent experience, employers, degrees, certifications, or numbers the candidate doesn't have. \
When a rewrite would benefit from a metric the resume lacks, use a bracketed placeholder like \
[X%] or [team size] for the candidate to fill in.
- Be candid. A weak match should score low; inflated scores don't help anyone get hired.
- Quote the resume's actual wording in `current` fields so the candidate can find it."""

REQUIREMENTS_RULES = """Requirements (job side):
- List every distinct requirement the posting states: skills/tools, years of experience, the role/title \
level, education, certifications. 8-20 items; split compound lines ("React and TypeScript") into separate items.
- importance "must" = stated as required / must / minimum / essential, or plainly core to the role. \
"nice" = preferred, bonus, plus, nice-to-have, familiarity with.
- keywords: 1-4 short literal terms an ATS would search for, most canonical first, including common \
variants (e.g. ["Kubernetes", "k8s"]). Empty for requirements with no searchable term.
- min_years: the number for experience requirements ("5+ years" -> 5), otherwise null.
- ids: "r1", "r2", ... in posting order."""

EVIDENCE_RULES = """Evidence (resume side), one entry per requirement id:
- status "met" = the resume clearly demonstrates it; "partial" = related or weaker evidence \
(adjacent tool, fewer years, mentioned without context); "missing" = nothing relevant.
- quote: copy the single most relevant line from the resume EXACTLY as written (it is checked verbatim). \
"" when missing.
- years: for experience requirements, the candidate's total years in that area computed from the resume's \
dates (null if not an experience requirement).
- Be strict and literal. Don't infer skills the resume doesn't show."""

EXTRACT_INSTRUCTIONS = f"""Extract the facts used to score this resume against the job. Do not score anything.

{REQUIREMENTS_RULES}

{EVIDENCE_RULES}"""

EVIDENCE_INSTRUCTIONS = f"""The job's requirements have already been extracted (below). Assess the resume against \
exactly these requirement ids — don't add, drop or reword requirements.

{EVIDENCE_RULES}"""

NARRATIVE_INSTRUCTIONS = """The scorecard above was computed by code from the requirement-by-requirement evidence. \
The scores are final: explain them, don't re-score or contradict them.

- rationales: one per category (all six), 1-2 sentences explaining that category's score in plain words, \
naming the specific requirements or resume lines behind it.
- verdict: one or two plain sentences consistent with the overall score and standing.
- strengths / gaps: 3-5 each, concrete.
- red_flags: things a recruiter might reject on (missing must-haves, mismatched seniority, gaps, \
location/visa issues stated in the posting). Empty list if none.
- changes: 6-12 specific edits, highest impact first. Prioritise missing or partial must-haves the \
candidate can truthfully strengthen, then missing keywords, then quantifying results. \
`suggested` is ready-to-paste replacement text."""

RESUME_EXTRACT_INSTRUCTIONS = """No job posting was given. Extract the facts used to score this resume on its own. Do not score anything.

- target_role: the job title this resume is best positioned for, based on the most recent roles.
- seniority: the level that title implies for this candidate.
- hard_skills: every concrete tool, technology, method or domain skill named in the resume, copied exactly as written.
- expected_keywords: 15 terms that recruiters and ATS filters most commonly look for in resumes for target_role at \
this seniority (tools, methods, domain terms). Include them whether or not this resume has them; use the most \
common short spelling (e.g. "Figma", "A/B testing", "SQL").
- roles: every position in the experience section, with start/end exactly as written ("" if missing) and the \
number of bullet or achievement lines under it."""

RESUME_NARRATIVE_INSTRUCTIONS = """No job posting was given, so this is a general resume review for the target role \
in the scorecard. The scorecard above was computed by code. The scores are final: explain them, don't re-score.

- rationales: one per category (all seven), 1-2 sentences explaining that category's score, naming specific \
resume lines.
- verdict: one or two plain sentences consistent with the overall score, and mention that adding a job link \
gives a match score for a specific role.
- strengths / gaps: 3-5 each, concrete.
- red_flags: things recruiters commonly reject on (unexplained gaps, job-hopping, typos, missing dates). \
Empty list if none.
- changes: 6-12 specific edits, highest impact first: missing expected keywords the candidate can truthfully \
add, quantifying results, stronger action verbs, then structure. `suggested` is ready-to-paste text."""

KIT_INSTRUCTIONS = """The candidate has the analysis above. Build their application kit so they stand out \
as a top candidate for this specific role.

- tailored_resume_markdown: the full resume rewritten for this job in clean Markdown (# name, contact line, \
## sections, - bullets). Apply the suggested changes, mirror the posting's language where it's truthful, \
lead bullets with strong verbs and outcomes, keep it to what fits on 1-2 pages. Keep every fact true; \
use [placeholders] for metrics the candidate must supply.
- projected_score: honest estimate of the score after these changes.
- positioning_statement: 2 sentences — the angle that makes this candidate memorable for this role.
- cover_letter: 250-350 words, specific to the company and role, no clichés, no "I am writing to apply".
- linkedin_connection_note: under 300 characters, for connecting with the recruiter or hiring manager.
- recruiter_message: a short LinkedIn DM (under 120 words) after connecting.
- hiring_manager_email_subject / _body: concise outreach email (under 150 words).
- who_to_contact: 3-5 job titles/roles to search for at the company on LinkedIn, plus how to find them.
- interview_talking_points: 5-7 likely questions for this role with an answer outline drawn from the \
candidate's real experience.
- application_checklist: ordered steps to apply and follow up, including timing."""


def _job_block(job: Job) -> str:
    meta = "\n".join(
        x for x in [f"Title: {job.title}", f"Company: {job.company}", f"Location: {job.location}", *job.criteria]
        if x.split(": ", 1)[-1]
    )
    return f"<job_posting>\n{meta}\n\n{job.description}\n</job_posting>"


# ---- Extraction cache ----

EXTRACT_VERSION = "1"  # bump when the extraction prompts change, to invalidate cached facts


def _key(*parts: str) -> str:
    return hashlib.sha256("\x00".join([EXTRACT_VERSION, model_name(), *parts]).encode()).hexdigest()[:32]


async def extract(resume: str, job: Job) -> tuple[JobRequirements, ResumeEvidence]:
    """Requirements are fixed once per job; evidence once per job+resume. Both are cached."""
    block = _job_block(job)
    job_key, pair_key = _key(block), _key(block, resume)
    reqs = await cache.get("jobs", job_key, JobRequirements)
    evidence = await cache.get("evidence", pair_key, ResumeEvidence)
    if reqs and evidence:
        return reqs, evidence
    if reqs:
        content = (
            f"{block}\n\n<requirements>\n{reqs.model_dump_json(indent=1)}\n</requirements>\n\n"
            f"<resume>\n{resume}\n</resume>\n\n{EVIDENCE_INSTRUCTIONS}"
        )
        evidence = await _parse(content, ResumeEvidence, effort="medium")
    else:
        content = f"{block}\n\n<resume>\n{resume}\n</resume>\n\n{EXTRACT_INSTRUCTIONS}"
        both = await _parse(content, Extraction, effort="medium")
        reqs, evidence = both.job, both.resume
        await cache.put("jobs", job_key, reqs)
    await cache.put("evidence", pair_key, evidence)
    return reqs, evidence


def _inline_refs(schema: dict) -> dict:
    """Gemini's JSON-schema support is safest without $ref/$defs, so expand them in place."""
    defs = schema.get("$defs", {})

    def walk(node):
        if isinstance(node, dict):
            if "$ref" in node:
                return walk(defs[node["$ref"].split("/")[-1]])
            return {k: walk(v) for k, v in node.items() if k != "$defs"}
        if isinstance(node, list):
            return [walk(x) for x in node]
        return node

    return walk(schema)


async def _gemini_parse(content: str, schema, effort: str):
    body = {
        "systemInstruction": {"parts": [{"text": SYSTEM}]},
        "contents": [{"role": "user", "parts": [{"text": content}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseJsonSchema": _inline_refs(schema.model_json_schema()),
            "maxOutputTokens": 32768,
            "thinkingConfig": {"thinkingLevel": "high" if effort == "high" else "low"},
        },
    }
    headers = {"x-goog-api-key": os.environ["GEMINI_API_KEY"]}

    async def generate(http: httpx.AsyncClient) -> httpx.Response:
        """Free-tier Gemini is often briefly overloaded (503) or over its per-minute quota (429):
        back off, then try the lighter model, which has its own quota."""
        resp = None
        for model in dict.fromkeys([GEMINI_MODEL, GEMINI_FALLBACK]):
            for delay in (0, 3, 8):
                await asyncio.sleep(delay)
                try:
                    resp = await http.post(f"{GEMINI_API}/{model}:generateContent", headers=headers, json=body)
                except httpx.HTTPError:
                    continue
                if resp.status_code not in (429, 500, 502, 503, 504):
                    return resp
        if resp is None:
            raise AIError("Couldn't reach the Gemini API. Check your connection.")
        return resp

    async with httpx.AsyncClient(timeout=300) as http:
        for attempt in range(2):
            resp = await generate(http)
            if resp.status_code in (401, 403):
                raise AIError("The Gemini API key is invalid. Check GEMINI_API_KEY.")
            if resp.status_code == 429:
                raise AIError("HireReady is busy right now. Try again in a minute.")
            if resp.status_code != 200:
                raise AIError(f"Gemini is busy right now ({resp.status_code}). Try again in a minute.")
            data = resp.json()
            cand = (data.get("candidates") or [{}])[0]
            reason = cand.get("finishReason")
            if reason == "MAX_TOKENS":
                raise AIError("The AI response was cut off. Try again, or shorten the resume/job text.")
            if reason in ("SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST") or "content" not in cand:
                raise AIError("The model declined this request.")
            text = "".join(p.get("text", "") for p in cand["content"].get("parts", []) if not p.get("thought"))
            try:
                return schema.model_validate_json(text)
            except ValidationError as e:
                if attempt:
                    raise AIError("The AI returned an incomplete answer. Try again.")
                body["contents"] += [
                    {"role": "model", "parts": [{"text": text}]},
                    {"role": "user", "parts": [{"text": f"That JSON didn't match the schema: {e}. Return the full corrected JSON."}]},
                ]


async def _parse(content: str, schema, effort: str):
    if provider() == "gemini":
        return await _gemini_parse(content, schema, effort)
    try:
        resp = await client().beta.messages.parse(
            model=MODEL,
            max_tokens=16000,
            system=SYSTEM,
            thinking={"type": "adaptive"},
            output_config={"effort": effort},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            messages=[{"role": "user", "content": content}],
            output_format=schema,
        )
    except anthropic.AuthenticationError:
        raise AIError("The Anthropic API key is invalid. Check ANTHROPIC_API_KEY.")
    except anthropic.RateLimitError:
        raise AIError("Rate limited by the Anthropic API. Try again in a minute.")
    except anthropic.BadRequestError as e:
        raise AIError(f"The AI request was rejected: {e.message}")
    except anthropic.APIStatusError as e:
        raise AIError(f"AI service error ({e.status_code}). Try again shortly.")
    except anthropic.APIConnectionError:
        raise AIError("Couldn't reach the Anthropic API. Check your connection.")

    if resp.stop_reason == "refusal":
        raise AIError("The model declined this request.")
    if resp.stop_reason == "max_tokens" or resp.parsed_output is None:
        raise AIError("The AI response was cut off. Try again, or shorten the resume/job text.")
    return resp.parsed_output


def _scorecard_block(card: scoring.Scorecard) -> str:
    data = {
        "overall_score": card.overall,
        "standing": card.standing,
        "categories": [c.model_dump(exclude={"rationale"}) for c in card.categories],
        "coverage": card.coverage.model_dump() if card.coverage else None,
        "requirements": [r.model_dump() for r in card.requirements],
    }
    return f"<scorecard>\n{json.dumps(data, indent=1)}\n</scorecard>"


async def analyze(resume: str, job: Job) -> Analysis:
    if demo_mode():
        from . import demo
        resume = demo.DEMO_RESUME
        extraction = Extraction.model_validate(demo.DEMO_EXTRACTION)
        reqs, evidence = extraction.job, extraction.resume
    else:
        reqs, evidence = await extract(resume, job)

    card = scoring.compute(resume, reqs, evidence)

    if demo_mode():
        narrative = Narrative.model_validate(demo.DEMO_NARRATIVE)
    else:
        content = (
            f"{_job_block(job)}\n\n<resume>\n{resume}\n</resume>\n\n"
            f"{_scorecard_block(card)}\n\n{NARRATIVE_INSTRUCTIONS}"
        )
        narrative = await _parse(content, Narrative, effort="medium")

    return scoring.assemble(
        card,
        narrative,
        candidate_name=evidence.candidate_name,
        job_title=reqs.job_title or job.title,
        company=reqs.company or job.company,
    )


async def analyze_resume(resume: str) -> Analysis:
    """General resume score when no job is given. Same split: model extracts facts, code scores."""
    if demo_mode():
        from . import demo
        resume = demo.DEMO_RESUME
        profile = ResumeProfile.model_validate(demo.DEMO_PROFILE)
    else:
        key = _key("resume-profile", resume)
        profile = await cache.get("profiles", key, ResumeProfile)
        if not profile:
            content = f"<resume>\n{resume}\n</resume>\n\n{RESUME_EXTRACT_INSTRUCTIONS}"
            profile = await _parse(content, ResumeProfile, effort="medium")
            await cache.put("profiles", key, profile)

    card = scoring.compute_resume(resume, profile)

    if demo_mode():
        narrative = ResumeNarrative.model_validate(demo.DEMO_RESUME_NARRATIVE)
    else:
        content = (
            f"<resume>\n{resume}\n</resume>\n\n<target_role>{profile.target_role}</target_role>\n\n"
            f"{_scorecard_block(card)}\n\n{RESUME_NARRATIVE_INSTRUCTIONS}"
        )
        narrative = await _parse(content, ResumeNarrative, effort="medium")

    return scoring.assemble_resume(card, narrative, profile)


async def build_kit(resume: str, job: Job, analysis: Analysis) -> ApplyKit:
    if demo_mode():
        from .demo import DEMO_KIT
        return ApplyKit.model_validate(DEMO_KIT)
    content = (
        f"{_job_block(job)}\n\n<resume>\n{resume}\n</resume>\n\n"
        f"<analysis>\n{json.dumps(analysis.model_dump(), indent=1)}\n</analysis>\n\n{KIT_INSTRUCTIONS}"
    )
    return await _parse(content, ApplyKit, effort="high")
