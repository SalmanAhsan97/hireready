"""Claude calls: extract requirements + evidence, write the narrative around a computed score, build the kit.

The score itself is computed in scoring.py. Extractions are cached on disk, so re-running the same
resume against the same job reuses the same facts and therefore gives the same score.
"""
import hashlib
import json
import os

import anthropic

from . import cache, scoring
from .models import Analysis, ApplyKit, Extraction, Job, JobRequirements, Narrative, ResumeEvidence

MODEL = os.getenv("HIREREADY_MODEL", "claude-opus-5-5")

_client: anthropic.AsyncAnthropic | None = None


class AIError(Exception):
    pass


def demo_mode() -> bool:
    if os.getenv("HIREREADY_DEMO") == "1":
        return True
    return not (os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN"))


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
    return hashlib.sha256("\x00".join([EXTRACT_VERSION, MODEL, *parts]).encode()).hexdigest()[:32]


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


async def _parse(content: str, schema, effort: str):
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
        "coverage": card.coverage.model_dump(),
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


async def build_kit(resume: str, job: Job, analysis: Analysis) -> ApplyKit:
    if demo_mode():
        from .demo import DEMO_KIT
        return ApplyKit.model_validate(DEMO_KIT)
    content = (
        f"{_job_block(job)}\n\n<resume>\n{resume}\n</resume>\n\n"
        f"<analysis>\n{json.dumps(analysis.model_dump(), indent=1)}\n</analysis>\n\n{KIT_INSTRUCTIONS}"
    )
    return await _parse(content, ApplyKit, effort="high")
