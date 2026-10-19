"""Model calls: extract requirements + evidence, write the narrative around a computed score, build the kit.

Uses Claude when an Anthropic key is set, otherwise Gemini (GEMINI_API_KEY), otherwise demo mode.

The score itself is computed in scoring.py. Extractions are cached on disk, so re-running the same
resume against the same job reuses the same facts and therefore gives the same score.
"""
import asyncio
import hashlib
import json
import os
from contextvars import ContextVar
import re

import anthropic
import httpx
from pydantic import ValidationError

from .settings import env
from . import cache, discover, scoring
from .models import (
    Analysis,
    ApplyKit,
    Extraction,
    Job,
    JobRequirements,
    Narrative,
    ChatMessage,
    CompareResult,
    LinkedInProfile,
    Discoverability,
    PostingsFacts,
    SearchMap,
    ProfileFromPdf,
    ProfileNarrative,
    ResumeEdit,
    ResumeEvidence,
    ResumeNarrative,
    ResumeProfile,
    RoleKeywords,
)

MODEL = env("MODEL", "claude-opus-5-5")
GEMINI_MODEL = env("GEMINI_MODEL", "gemini-flash-latest")
GEMINI_FALLBACK = env("GEMINI_FALLBACK", "gemini-flash-lite-latest")
GEMINI_API = "https://generativelanguage.googleapis.com/v1beta/models"
# Paid plans: a stronger model for the writing (rationales, rewrites, chat, application kit), e.g.
# "claude-opus-5-5" or "gemini-pro-latest". Fact extraction always uses the standard model, so a resume's
# score never depends on the plan. Empty = everyone gets the standard model.
PREMIUM_MODEL = env("PREMIUM_MODEL", "").strip()
_premium: ContextVar[bool] = ContextVar("premium", default=False)


def set_plan(paid: bool) -> None:
    """Called per request: paid requests get PREMIUM_MODEL for writing tasks."""
    _premium.set(paid)

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
    return env("DEMO") == "1" or provider() is None


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
- Write to the candidate directly ("you", "your"). Never refer to them in the third person or guess their pronouns.
- Quote the resume's actual wording in `current` fields so the candidate can find it.
- Write like a specific, credible person: no generic phrases ("passionate", "results-driven", "dynamic", \
"detail-oriented", "team player", "proven track record"). Show the action, the context and the result instead.
- Mention work authorisation (e.g. permanent resident, authorised to work) at most once, and only if it's in the \
resume or the posting asks. Don't repeat the same metric in the summary and the bullets."""

REQUIREMENTS_RULES = """Requirements (job side):
- List every distinct requirement the posting states: skills/tools, years of experience, the role/title \
level, education, certifications. 10-25 items.
- ONE skill, tool or method per requirement. Split lists: "paid ads, SEO, email and social media" is four \
requirements ("Paid ads", "SEO", "Email marketing", "Social media marketing"), each with its own keywords.
- Don't split one tool into its features: "Expert in Figma, including auto layout and variables" is ONE requirement \
("Expert in Figma") whose keywords may include the features. Don't list the same skill twice.
- importance "must" = stated as required / must / minimum / essential, or plainly core to the role. \
"nice" = preferred, bonus, plus, nice-to-have, familiarity with.
- keywords: 1-4 short literal terms an ATS would search for, most canonical first, including common \
variants (e.g. ["Kubernetes", "k8s"]). Empty for requirements with no searchable term.
- min_years: the number for experience requirements ("5+ years" -> 5), otherwise null.
- ids: "r1", "r2", ... in posting order.
- gates: hard eligibility conditions the posting states that a resume can't prove, as short phrases \
(e.g. "Authorised to work in Canada", "Onsite in Toronto 3 days a week", "Secret clearance", "Valid driver's licence", \
"Willing to travel 30%"). Not skills or experience. Empty list if none."""

EVIDENCE_RULES = """Evidence (resume side), one entry per requirement id:
- status "met" = the resume clearly demonstrates it; "partial" = related or weaker evidence \
(adjacent tool, fewer years, mentioned without context); "missing" = nothing relevant.
- quote: copy the single most relevant line from the resume EXACTLY as written (it is checked verbatim). \
"" when missing.
- years: for experience requirements, the candidate's years in that area: add up the code-computed durations \
in <dates> for the roles that count (null if not an experience requirement). Never do your own date math.
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
- target_role should be a common, searchable job title (e.g. "Marketing Manager", not a niche internal title).
- hard_skills: every concrete tool, technology, method or domain skill named in the resume, copied exactly as written.
- roles: every position in the experience section, with start/end exactly as written ("" if missing) and the \
number of bullet or achievement lines under it."""

ROLE_KEYWORDS_INSTRUCTIONS = """List the 20 terms that most often appear as requirements in job postings for a {seniority}-level \
{role}: tools, platforms, methods, metrics and domain terms an ATS would search for. No soft skills or generic \
traits ("communication", "team player"). Use the most common short spelling (e.g. "Google Ads", "A/B testing", \
"SQL"), most important first. For each, give aliases: the common variants and abbreviations an ATS would treat \
as the same skill (e.g. "Google Analytics" -> ["GA4", "Google Analytics 4"]; "Pay-per-click" -> ["PPC", \
"paid search"]). Empty list if none."""

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

CHAT_INSTRUCTIONS = """You are editing the candidate's resume with them, one request at a time.

- Apply the latest user request to <current_resume> and return the FULL updated resume in clean Markdown \
(# Name, a contact line, ## sections, - bullets). On the first edit, convert the original resume to that format \
without changing its content, then apply the request.
- Change only what the request asks for; keep everything else word-for-word.
- Truthfulness is the top rule. Use ONLY facts stated in the original resume or by the user in this conversation. \
Never invent or inflate employers, titles, dates, years of experience, degrees, certifications, tools, skills, \
responsibilities (e.g. budgets, team sizes, revenue) or numbers. Keep existing numbers exactly as written \
("4+ years" stays "4+ years"). You may echo the job's wording only for things the resume already shows.
- Where a metric would help but isn't known, insert a [placeholder] such as [X%] or [N clients] and say so.
- If the request needs facts you don't have, or would make the resume untruthful, set changed=false, return the \
resume unchanged, and ask for what you need in `reply`.
- reply: 1-3 friendly sentences on what you did (or what you need). changes: one short line per content edit \
(don't mention the Markdown conversion).
- Use the analysis and job (when given) to make suggestions specific, e.g. "apply the high-priority changes"."""

PROFILE_PDF_INSTRUCTIONS = """This is a LinkedIn profile exported with "Save to PDF". Extract it exactly as written.
- headline: the line under the name. about: the full Summary/About text ("" if none).
- experiences: every position, most recent first, with its full description ("" if none).
- top_skills: the "Top Skills" list. certifications / education: how many entries each section has.
- profile_url: the linkedin.com/in/... URL from the contact section ("" if none).
- contact_email: true if the Contact section shows an email address. contact_websites: how many non-LinkedIn \
links (portfolio, company, GitHub, blog) the Contact section lists."""

PROFILE_NARRATIVE_INSTRUCTIONS = """Review this LinkedIn profile for someone targeting <target_role>. The scorecard above was \
computed by code from the profile; the scores are final — explain them, don't re-score.

- rationales: one per category in the scorecard (skip ones with weight 0), 1-2 sentences naming specific parts \
of the profile.
- verdict: one or two plain sentences on how findable and convincing this profile is to recruiters searching for \
<target_role>.
- strengths / gaps: 3-5 each, concrete. red_flags: things that put recruiters off (empty headline, gaps, \
titles that don't match the target, short stints or gaps from Career timeline, "open to work" mixed signals). \
Empty list if none.
- changes: 6-10 edits, highest impact first. Include a rewritten headline (under 220 characters) and an About \
opening (first 2-3 lines) as `suggested` text. `section` is the profile section (Headline, About, Experience — \
<company>, Skills, Featured...). `current` quotes the profile.
- Truthfulness: use only facts on the profile. Where a metric would help, use a [placeholder].
- top_half: exactly 3 versions, each a headline (under 220 characters, leading with the target title or a title \
variant from <search_map>) and an about_opening (the first 2-3 lines of About, what shows before "see more"). Build \
them only from facts on the profile; prefer essential terms the <evidence_map> marks "proven" or "claimed". Never \
name a "missing" term. No buzzwords or generic phrases. Make the three genuinely different angles.
- experience_rewrites: for the 2-4 most relevant roles, a description framed as problem -> action -> result -> \
skill proved. Use only what that role's description (or the About) says; where the result has no number on the \
profile, write a [placeholder] such as [X%] or [N customers]. `skill` is the search-map term the entry proves. \
`text` is the ready-to-paste description in first person, 2-3 sentences.
- proof_questions: for up to 5 essential or useful terms the <evidence_map> marks "missing" or "claimed", one short \
question asking whether and where the candidate has done it. Ask; never assume they have."""

POSTINGS_INSTRUCTIONS = """These are recent job postings for <role>. For EACH posting, list what it asks for. Do not count, \
rank or merge postings.
- index: the posting's number from <posting n="...">.
- title: the posting's job title in its common searchable form, without seniority words, levels, location or team \
("Sr. Growth Marketing Manager II - EMEA" -> "Growth Marketing Manager").
- industry: the employer's industry in 1-3 words ("SaaS", "Banking", "E-commerce"), "" if unclear.
- skills: methods and domain skills the posting asks for ("SEO", "A/B testing", "stakeholder management"), short \
canonical spelling. No soft traits ("communication", "team player", "self-starter").
- tools: named software, platforms and languages ("HubSpot", "SQL", "Google Ads").
- outcomes: results the role is measured on, 2-4 words each ("pipeline growth", "customer retention"). Empty if none.
- Spell the same thing the same way in every posting."""

KIT_INSTRUCTIONS = """The candidate has the analysis above. Build their application kit so they stand out \
as a top candidate for this specific role.

- tailored_resume_markdown: the full resume rewritten for this job in clean Markdown (# name, contact line, \
## sections, - bullets). Apply the suggested changes, mirror the posting's language where it's truthful, \
lead bullets with strong verbs and outcomes, keep it to what fits on 1-2 pages. Keep every fact true; \
use [placeholders] for metrics the candidate must supply.
- projected_score: honest estimate of the score after these changes.
- positioning_statement: 2 sentences — the angle that makes this candidate memorable for this role.
- cover_letter: 250-350 words, specific to the company and role, no clichés, no "I am writing to apply". \
Don't invent a hiring manager's or recruiter's name: use one only if the posting gives it, otherwise "Hiring team".
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

EXTRACT_VERSION = "4"  # bump when the extraction prompts change, to invalidate cached facts
# Readings per resume+job pair, majority-voted (1 = single read). Costs that many model calls on a pair's first read only.
EVIDENCE_VOTES = max(1, int(env("EVIDENCE_VOTES", "3")))


def _key(*parts: str) -> str:
    return hashlib.sha256("\x00".join([EXTRACT_VERSION, model_name(), *parts]).encode()).hexdigest()[:32]


async def extract(resume: str, job: Job) -> tuple[JobRequirements, ResumeEvidence]:
    """Requirements are fixed once per job; evidence once per job+resume. Both are cached."""
    block = _job_block(job)
    dates = scoring.durations_block(resume)
    job_key, pair_key = _key(block), _key(block, resume)
    reqs = await cache.get("jobs", job_key, JobRequirements)
    evidence = await cache.get("evidence", pair_key, ResumeEvidence)
    if reqs and evidence:
        return reqs, evidence
    if reqs:
        evidence = await _voted_evidence(block, reqs, resume)
    else:
        content = f"{block}\n\n<resume>\n{resume}\n</resume>\n\n{dates}\n\n{EXTRACT_INSTRUCTIONS}"
        both = await _parse(content, Extraction, effort="medium")
        reqs = both.job
        await cache.put("jobs", job_key, reqs)
        evidence = await _voted_evidence(block, reqs, resume, first=both.resume)
    await cache.put("evidence", pair_key, evidence)
    return reqs, evidence


async def _voted_evidence(block: str, reqs: JobRequirements, resume: str, first: ResumeEvidence | None = None) -> ResumeEvidence:
    """Read the resume against the requirements EVIDENCE_VOTES times and keep each requirement's median verdict.
    Only runs on a resume+job pair's first read; the result is cached. A failed extra reading just means fewer votes."""
    needed = EVIDENCE_VOTES - (first is not None)
    results = await asyncio.gather(*(_evidence(block, reqs, resume) for _ in range(max(0, needed))), return_exceptions=True)
    votes = ([first] if first else []) + [r for r in results if isinstance(r, ResumeEvidence)]
    if not votes:
        raise next(r for r in results if isinstance(r, Exception))
    return scoring.merge_evidence(votes, resume)


async def _evidence(block: str, reqs: JobRequirements, resume: str) -> ResumeEvidence:
    content = (
        f"{block}\n\n<requirements>\n{reqs.model_dump_json(indent=1)}\n</requirements>\n\n"
        f"<resume>\n{resume}\n</resume>\n\n{scoring.durations_block(resume)}\n\n{EVIDENCE_INSTRUCTIONS}"
    )
    return await _parse(content, ResumeEvidence, effort="medium")


async def compare_resumes(resumes: list[tuple[str, str]], job: Job) -> CompareResult:
    """Score 2-4 resumes against ONE fixed set of requirements, so the numbers are comparable.
    No narrative call: the comparison and verdict are computed in code."""
    if demo_mode():
        from . import demo
        extraction = Extraction.model_validate(demo.DEMO_EXTRACTION)
        reqs, evidence = extraction.job, [extraction.resume] * len(resumes)
    else:
        reqs, first = await extract(resumes[0][1], job)  # fixes (and caches) the job's requirements
        block = _job_block(job)

        async def one(text: str) -> ResumeEvidence:
            key = _key(block, text)
            ev = await cache.get("evidence", key, ResumeEvidence)
            if not ev:
                ev = await _voted_evidence(block, reqs, text)
                await cache.put("evidence", key, ev)
            return ev

        evidence = [first, *await asyncio.gather(*(one(text) for _, text in resumes[1:]))]
    scored = [(name, scoring.compute(text, reqs, ev)) for (name, text), ev in zip(resumes, evidence)]
    result = scoring.compare(reqs, scored)
    if not result.job_title:
        result.job_title, result.company = job.title, job.company
    return result


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


async def _gemini_parse(content: str, schema, effort: str, models: list[str] | None = None, writing: bool = False):
    body = {
        "systemInstruction": {"parts": [{"text": SYSTEM}]},
        "contents": [{"role": "user", "parts": [{"text": content}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseJsonSchema": _inline_refs(schema.model_json_schema()),
            "maxOutputTokens": 32768,
            "thinkingConfig": {"thinkingLevel": "high" if effort == "high" else "low"},
            # facts that feed a score are read at temperature 0 so the first read of a job is as stable as possible
            **({} if writing else {"temperature": 0}),
        },
    }
    headers = {"x-goog-api-key": os.environ["GEMINI_API_KEY"]}

    async def generate(http: httpx.AsyncClient) -> httpx.Response:
        """Free-tier Gemini is often briefly overloaded (503) or over its per-minute quota (429):
        back off, then try the lighter model, which has its own quota."""
        resp = None
        for model in dict.fromkeys(models or [GEMINI_MODEL, GEMINI_FALLBACK]):
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
                raise AIError("Screenproof is busy right now. Try again in a minute.")
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


def _premium_route() -> str | None:
    """Which API serves PREMIUM_MODEL, if it's configured and that API has a key."""
    if not (PREMIUM_MODEL and _premium.get()):
        return None
    if PREMIUM_MODEL.startswith("gemini") and os.getenv("GEMINI_API_KEY"):
        return "gemini"
    if PREMIUM_MODEL.startswith("claude") and (os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN")):
        return "anthropic"
    return None


async def _parse(content: str, schema, effort: str, writing: bool = False):
    """writing=True marks tasks whose output is prose for the user (never facts that feed a score)."""
    route = _premium_route() if writing else None
    if route:
        try:
            if route == "gemini":
                return await _gemini_parse(content, schema, "high", models=[PREMIUM_MODEL], writing=True)
            return await _anthropic_parse(content, schema, effort, PREMIUM_MODEL)
        except AIError:
            pass  # the paid model being busy shouldn't fail the request: fall back to the standard one
    if provider() == "gemini":
        return await _gemini_parse(content, schema, effort, writing=writing)
    return await _anthropic_parse(content, schema, effort, MODEL)


async def _anthropic_parse(content: str, schema, effort: str, model: str):
    try:
        resp = await client().beta.messages.parse(
            model=model,
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
        narrative = await _parse(content, Narrative, effort="medium", writing=True)

    analysis = scoring.assemble(
        card,
        narrative,
        candidate_name=evidence.candidate_name,
        job_title=reqs.job_title or job.title,
        company=reqs.company or job.company,
    )
    changes, projected = scoring.edit_gains(resume, analysis.changes, lambda t: scoring.compute(t, reqs, evidence).overall, card.overall)
    return analysis.model_copy(update={"changes": changes, "projected_score": projected, "writing_flags": scoring.writing_flags(resume),
                                       "assessment": scoring.assess(card, job.description, resume, projected)})


async def role_keywords(role: str, seniority: str) -> list:
    """Keywords expected for a role, generated WITHOUT the resume (so they can't be biased toward it) and
    cached per role, so every resume for the same role is measured against the same list."""
    norm = " ".join(role.lower().split())
    key = _key("role-keywords-v2", norm, seniority)
    cached = await cache.get("roles", key, RoleKeywords)
    if not cached:
        prompt = ROLE_KEYWORDS_INSTRUCTIONS.format(role=role, seniority=seniority)
        cached = await _parse(prompt, RoleKeywords, effort="low")
        await cache.put("roles", key, cached)
    return cached.keywords[:20]


async def analyze_resume(resume: str) -> Analysis:
    """General resume score when no job is given. Same split: model extracts facts, code scores."""
    if demo_mode():
        from . import demo
        resume = demo.DEMO_RESUME
        profile = ResumeProfile.model_validate(demo.DEMO_PROFILE)
    else:
        key = _key("resume-profile-v2", resume)
        profile = await cache.get("profiles", key, ResumeProfile)
        if not profile:
            content = f"<resume>\n{resume}\n</resume>\n\n{RESUME_EXTRACT_INSTRUCTIONS}"
            profile = await _parse(content, ResumeProfile, effort="medium")
            await cache.put("profiles", key, profile)
        profile.expected_keywords = await role_keywords(profile.target_role, profile.seniority)

    card = scoring.compute_resume(resume, profile)

    if demo_mode():
        narrative = ResumeNarrative.model_validate(demo.DEMO_RESUME_NARRATIVE)
    else:
        content = (
            f"<resume>\n{resume}\n</resume>\n\n<target_role>{profile.target_role}</target_role>\n\n"
            f"{_scorecard_block(card)}\n\n{RESUME_NARRATIVE_INSTRUCTIONS}"
        )
        narrative = await _parse(content, ResumeNarrative, effort="medium", writing=True)

    analysis = scoring.assemble_resume(card, narrative, profile)
    changes, projected = scoring.edit_gains(resume, analysis.changes, lambda t: scoring.compute_resume(t, profile).overall, card.overall)
    return analysis.model_copy(update={"changes": changes, "projected_score": projected, "writing_flags": scoring.writing_flags(resume)})


async def edit_resume(resume: str, current_md: str, messages: list[ChatMessage], job: Job | None, analysis: Analysis | None) -> ResumeEdit:
    if demo_mode():
        from .demo import DEMO_CHAT_REPLY
        return ResumeEdit(reply=DEMO_CHAT_REPLY, changed=False, changes=[], resume_markdown=current_md or resume)
    history = "\n".join(f"<{m.role}>{m.content}</{m.role}>" for m in messages[-10:])
    context = ""
    if job and job.description:
        context += _job_block(job) + "\n\n"
    if analysis:
        brief = {k: analysis.model_dump()[k] for k in ("overall_score", "job_title", "missing_keywords", "gaps", "changes")}
        context += f"<analysis>\n{json.dumps(brief, indent=1)}\n</analysis>\n\n"
    content = (
        f"{context}<original_resume>\n{resume}\n</original_resume>\n\n"
        f"<current_resume>\n{current_md or '(not converted yet — use the original)'}\n</current_resume>\n\n"
        f"<conversation>\n{history}\n</conversation>\n\n{CHAT_INSTRUCTIONS}"
    )
    edit = await _parse(content, ResumeEdit, effort="medium", writing=True)
    if not edit.changed or not edit.resume_markdown.strip():
        edit.resume_markdown = current_md or edit.resume_markdown or resume
        return edit
    # Safety net: flag any figure that appears in neither the original resume nor the user's own messages.
    known = resume + " " + " ".join(m.content for m in messages if m.role == "user")
    figures = new_figures(edit.resume_markdown, known)
    if figures:
        edit.changes.append("Please verify — these figures aren't in your original resume: " + ", ".join(figures[:8]))
    return edit


async def profile_from_pdf_text(text: str) -> LinkedInProfile:
    key = _key("li-pdf", text)
    cached = await cache.get("li-profiles", key, LinkedInProfile)
    if cached:
        return cached
    if demo_mode():
        raise AIError("Reading a LinkedIn PDF needs an AI model, and Screenproof isn't connected to one right now.")
    pdf = await _parse(f"<linkedin_pdf>\n{text}\n</linkedin_pdf>\n\n{PROFILE_PDF_INSTRUCTIONS}", ProfileFromPdf, effort="low")
    slug = re.search(r"linkedin\.com/in/([^/\s?#]+)", pdf.profile_url or text)
    profile = LinkedInProfile(
        name=pdf.name, headline=pdf.headline, about=pdf.about, location=pdf.location,
        experiences=pdf.experiences, skills=pdf.top_skills,
        skills_total=None, certifications=pdf.certifications, recommendations=None, education=pdf.education,
        connections=None, has_photo=None, has_banner=None, has_featured=None,
        custom_url=(not re.search(r"-[0-9a-f]{6,}$|\d{5,}$", slug.group(1))) if slug else None,
        source="LinkedIn PDF export", has_contact=pdf.contact_email or pdf.contact_websites > 0,
    )
    await cache.put("li-profiles", key, profile)
    return profile


SEARCH_MAP_POSTINGS = 15
MAX_MAP_POSTINGS = 30
SEARCH_MAP_CACHE_S = 7 * 86400
POSTING_CHARS = 3000


def guess_country(location: str) -> str:
    from .jobs import COUNTRIES
    low = location.lower()
    aliases = {"united states": "USA", "united kingdom": "UK", "england": "UK", "emirates": "UAE", "dubai": "UAE"}
    return next((c for c in COUNTRIES if c.lower() in low), None) or next((v for k, v in aliases.items() if k in low), "USA")


def merge_postings(*groups: list) -> list:
    """One list, first group first, without the same job twice (same title at the same company)."""
    out, seen = [], set()
    for group in groups:
        for p in group:
            k = (scoring._norm(p.title), scoring._norm(p.company))
            if k not in seen:
                seen.add(k)
                out.append(p)
    return out[:MAX_MAP_POSTINGS]


async def search_map(role: str, country: str, allowed: bool = False) -> SearchMap | None:
    """What recruiters search for this role, counted over real recent postings (Indeed, plus LinkedIn via Bright
    Data when enabled). One model call reads the postings; the counting is code. Cached per role+country for a week.
    allowed: this request may spend Bright Data records (Pro/admin). None when there aren't enough postings."""
    from . import jobs, linkedin_jobs
    base = " ".join(role.lower().split())
    li_key, in_key = _key("search-map-v2", base, country, "li"), _key("search-map-v2", base, country, "in")
    cached = await cache.get("search-maps", li_key, SearchMap, max_age_s=SEARCH_MAP_CACHE_S)
    if cached:
        return cached

    async def indeed() -> list:
        try:
            return await jobs.search(role, "", country, limit=SEARCH_MAP_POSTINGS)
        except jobs.JobSearchError:
            return []

    if allowed and linkedin_jobs.enabled():
        found_in, found_li = await asyncio.gather(indeed(), linkedin_jobs.search(role, country, allowed=True))
    else:
        found_li = await linkedin_jobs.search(role, country, allowed=False)  # only postings already paid for
        found_in = None
    if not found_li:
        cached = await cache.get("search-maps", in_key, SearchMap, max_age_s=SEARCH_MAP_CACHE_S)
        if cached:
            return cached
    if found_in is None:
        found_in = await indeed()
    postings = merge_postings(found_li, found_in)
    if len(postings) < discover.MIN_POSTINGS:
        return None
    blocks = "\n\n".join(f'<posting n="{i}">\nTitle: {p.title}\n{p.description[:POSTING_CHARS]}\n</posting>' for i, p in enumerate(postings, 1))
    try:
        facts = await _parse(f"<role>{role}</role>\n\n{blocks}\n\n{POSTINGS_INSTRUCTIONS}", PostingsFacts, effort="low")
    except AIError:
        return None  # the review still runs on the estimated keyword list
    smap = discover.build_search_map(role, country, postings, facts.postings)
    names = {"linkedin": "LinkedIn", "indeed": "Indeed"}
    for p in postings:
        name = names.get(p.site, p.site.title() or "Other")
        smap.sources[name] = smap.sources.get(name, 0) + 1
    await cache.put("search-maps", li_key if found_li else in_key, smap)
    return smap


def _demo_search_map(role: str, country: str) -> SearchMap:
    from types import SimpleNamespace
    from . import demo
    from .models import PostingFacts
    postings = [SimpleNamespace(title=t, description=d) for t, d in demo.DEMO_POSTINGS]
    smap = discover.build_search_map(role, country, postings, [PostingFacts.model_validate(f) for f in demo.DEMO_POSTING_FACTS])
    smap.sources = {"Indeed": len(postings)}
    return smap


def _map_block(smap: SearchMap) -> str:
    terms = [t.model_dump(include={"term", "kind", "share", "label"}) for t in smap.terms if t.label != "noise"][:40]
    return f"<search_map postings=\"{smap.postings}\">\n{json.dumps(terms, indent=1)}\n</search_map>"


async def analyze_profile(profile: LinkedInProfile, target_role: str, country: str = "", allowed: bool = False) -> Analysis:
    """Same split as the resume: code scores the profile's facts and runs the recruiter searches, the model
    explains and writes the rewrites (which code then checks)."""
    role = target_role.strip() or (profile.experiences[0].title if profile.experiences else "") or profile.headline[:60]
    country = country or guess_country(profile.location)
    if demo_mode():
        smap = _demo_search_map(role, country)
    else:
        smap = await search_map(role, country, allowed)
        if smap is None:
            smap = discover.estimated_map(role, await role_keywords(role, "mid"))
    weighted = discover.scored_terms(smap)
    card = scoring.compute_profile(profile, role, [t for t, _ in weighted], weights={t.term: w for t, w in weighted},
                                   postings=smap.postings)
    evidence = discover.evidence_map(profile, smap)
    searches = discover.discoverability(profile, role, smap)
    if demo_mode():
        from . import demo
        narrative = ProfileNarrative.model_validate(demo.DEMO_LI_NARRATIVE)
    else:
        brief = profile.model_dump(exclude={"source"})
        content = (
            f"<target_role>{role}</target_role>\n\n<linkedin_profile>\n{json.dumps(brief, indent=1)}\n</linkedin_profile>\n\n"
            f"{_map_block(smap)}\n\n<evidence_map>\n{json.dumps([e.model_dump() for e in evidence], indent=1)}\n</evidence_map>\n\n"
            f"<recruiter_searches>\n{json.dumps([c.model_dump() for c in searches], indent=1)}\n</recruiter_searches>\n\n"
            f"{_scorecard_block(card)}\n\n{PROFILE_NARRATIVE_INSTRUCTIONS}"
        )
        narrative = await _parse(content, ProfileNarrative, effort="medium", writing=True)
    analysis = scoring.assemble_profile(card, narrative, profile, role)
    known = " ".join([profile.headline, profile.about, *(f"{e.title} {e.company} {e.description}" for e in profile.experiences), *profile.skills])
    missing = [e.term for e in evidence if e.status == "missing"]
    asked = {discover.canon(e.term) for e in evidence if e.status != "proven"}
    top = [t.model_copy(update={"warnings": discover.rewrite_warnings(f"{t.headline} {t.about_opening}", known, missing)
                                + ([f"Headline is {len(t.headline)} characters; LinkedIn allows 220."] if len(t.headline) > 220 else [])})
           for t in narrative.top_half[:3]]
    exp = [x.model_copy(update={"warnings": discover.rewrite_warnings(x.text, known, missing)}) for x in narrative.experience_rewrites[:4]]
    questions = [q for q in narrative.proof_questions if discover.canon(q.term) in asked][:5]
    analysis.discover = Discoverability(
        search_map=smap, evidence=evidence, searches=searches, target_titles=discover.target_titles(role, smap),
        top_half=top, experience_rewrites=exp, proof_questions=questions,
    )
    return analysis


async def build_kit(resume: str, job: Job, analysis: Analysis) -> ApplyKit:
    if demo_mode():
        from .demo import DEMO_KIT
        return ApplyKit.model_validate(DEMO_KIT)
    content = (
        f"{_job_block(job)}\n\n<resume>\n{resume}\n</resume>\n\n"
        f"<analysis>\n{json.dumps(analysis.model_dump(), indent=1)}\n</analysis>\n\n{KIT_INSTRUCTIONS}"
    )
    kit = await _parse(content, ApplyKit, effort="high", writing=True)
    # Safety net: figures in the tailored resume or outreach that appear in neither the resume nor the posting
    known = f"{resume} {job.description} {job.title} {job.company}"
    for label, text in (("tailored resume", kit.tailored_resume_markdown), ("cover letter", kit.cover_letter),
                        ("outreach", f"{kit.recruiter_message} {kit.hiring_manager_email_body}")):
        figures = new_figures(text, known)
        if figures:
            kit.warnings.append(f"Check the {label}: these figures aren't in your resume or the posting: " + ", ".join(figures[:8]))
    return kit


def new_figures(text: str, known: str) -> list[str]:
    """Numbers in `text` (ignoring [placeholders]) that appear nowhere in `known`."""
    unbracketed = re.sub(r"\[[^\]]*\]", " ", text)
    return sorted({n for n in re.findall(r"\d[\d,.]*\+?%?", unbracketed) if n.rstrip(".,") not in known})
