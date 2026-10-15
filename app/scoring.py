"""Deterministic scoring: the same resume text + extraction always yields the same score.

Claude only extracts facts (the job's requirements and the resume's quoted evidence for each).
Everything numeric happens here: evidence is checked against the resume text, keywords are matched
literally like an ATS would, and the weighted overall score is computed from those results.
"""
import re
from dataclasses import dataclass
from typing import Iterable, List, Optional

from .models import (
    Analysis,
    CategoryScore,
    Coverage,
    Evidence,
    JobRequirements,
    Narrative,
    Requirement,
    RequirementResult,
    ResumeEvidence,
    ResumeNarrative,
    ResumeProfile,
)

# Priorities follow Jobscan's published order (hard skills > education > job title > soft skills/keywords);
# impact and formatting matter to human reviewers, so they keep a small share.
WEIGHTS = {
    "Hard skills & tools": 40,
    "Relevant experience & seniority": 20,
    "ATS keyword coverage": 15,
    "Impact & quantified results": 10,
    "Education & certifications": 10,
    "Clarity & formatting": 5,
}
CREDIT = {"met": 1.0, "partial": 0.5, "missing": 0.0}
IMPORTANCE = {"must": 2, "nice": 1}
# Half of all bullet-like lines carrying a number earns full marks for impact.
QUANTIFIED_TARGET = 0.5


def standing_for(score: int) -> str:
    if score >= 80:
        return "top candidate"
    if score >= 65:
        return "competitive"
    if score >= 45:
        return "possible"
    return "long shot"


# ---- Text matching ----

def _norm(text: str) -> str:
    """Lowercase, keep characters that matter in tech terms (c++, c#, .net), collapse the rest."""
    return re.sub(r"[^a-z0-9+#.]+", " ", text.lower()).strip()


def _tokens(text: str) -> List[str]:
    return re.findall(r"[a-z0-9+#]+", text.lower())


def keyword_hits(keywords: Iterable[str], resume_norm: str) -> List[str]:
    hits = []
    for kw in keywords:
        k = _norm(kw)
        if not k:
            continue
        exact = rf"(?<![a-z0-9]){re.escape(k)}(?:s|es)?(?![a-z0-9])"
        # multi-word terms may be split by a word or two: "Meta & Google Ads" covers "Meta Ads"
        words = k.split()
        near = r"(?<![a-z0-9])" + r"(?:\s+\S+){0,2}?\s+".join(map(re.escape, words)) + r"(?:s|es)?(?![a-z0-9])" if len(words) > 1 else None
        if (re.search(exact, resume_norm) or (near and re.search(near, resume_norm))) and kw not in hits:
            hits.append(kw)
    return hits


def quote_found(quote: str, resume_tokens: str, resume_vocab: set) -> bool:
    """True if Claude's quote actually appears in the resume (punctuation/spacing-insensitive)."""
    fragments = [f for f in re.split(r"\.\.\.|…", quote) if _tokens(f)]
    if not fragments:
        return False
    for frag in fragments:
        toks = _tokens(frag)
        if f" {' '.join(toks)} " in resume_tokens:
            continue
        # Tolerate PDF extraction artefacts (hyphenation, merged words): most words must still be there.
        if len(toks) < 3 or sum(t in resume_vocab for t in toks) / len(toks) < 0.85:
            return False
    return True


# ---- Degrees (compared in code, so "Master's" always satisfies "Bachelor's required") ----

DEGREE_PATTERNS = [
    (4, r"\b(ph\.?\s?d|doctorate|doctoral|d\.?phil)\b"),
    (3, r"\b(masters?|master's|m\.?sc|msc|mba|m\.?eng|m\.?phil|mphil|ms|m\.s\.|ma|m\.a\.|postgraduate|graduate degree)\b"),
    (2, r"\b(bachelors?|bachelor's|b\.?sc|bsc|bba|b\.?eng|b\.?com|bcom|bs|b\.s\.|ba|b\.a\.|undergraduate|4-year degree|four-year degree)\b"),
    (1, r"\b(associates?|associate's|diploma|a-levels?|high school)\b"),
]
DEGREE_NAMES = {4: "a doctorate", 3: "a master's", 2: "a bachelor's", 1: "a diploma"}
NOT_DEGREE = r"\bms (office|excel|word|powerpoint|teams|project|access|outlook|dynamics)\b"


def degree_levels(text: str) -> list[int]:
    t = re.sub(NOT_DEGREE, " ", text.lower().replace("’", "'"))
    return [lvl for lvl, pat in DEGREE_PATTERNS if re.search(pat, t)]


def resume_degree(resume: str) -> int | None:
    """Highest degree in the education section (or on lines that mention a school), else None."""
    lines, in_edu, picked = resume.splitlines(), False, []
    for line in lines:
        if re.match(r"^\W*(education|academic)", line, re.I):
            in_edu = True
            continue
        if in_edu and HEADING_LINE.match(line.strip()):
            in_edu = False
        if in_edu or re.search(r"universit|college|institute|school of|degree", line, re.I):
            picked.append(line)
    levels = degree_levels("\n".join(picked))
    return max(levels) if levels else None


HEADING_LINE = re.compile(r"^[A-Z][A-Z &/]{3,}$")


# ---- Requirement coverage ----

def _assess(req: Requirement, ev: Optional[Evidence], resume_norm: str, resume_tokens: str, vocab: set, resume_text: str) -> RequirementResult:
    hits = keyword_hits(req.keywords, resume_norm)
    quote = (ev.quote if ev else "").strip()
    verified = bool(quote) and quote_found(quote, resume_tokens, vocab)
    claimed = ev.status if ev else "missing"
    note = ""

    needed = degree_levels(req.text) if req.kind == "education" else []
    have = resume_degree(resume_text) if needed else None
    if needed and have is not None:
        need = min(needed)  # "Bachelor's or Master's" -> a bachelor's is the minimum
        status = "met" if have >= need else "partial" if have == need - 1 else "missing"
        note = f"You have {DEGREE_NAMES[have]}; this asks for {DEGREE_NAMES[need]}"
    elif req.kind == "experience" and req.min_years and ev and ev.years is not None:
        ratio = ev.years / req.min_years
        status = "met" if ratio >= 1 else "partial" if ratio >= 0.75 else "missing"
        note = f"{ev.years:g} years (asks for {req.min_years}+)"
    elif claimed == "met":
        status = "met" if verified or hits else "partial"
        if status == "partial":
            note = "Claimed, but no supporting text found in the resume"
    elif claimed == "partial":
        status = "partial" if verified or hits else "missing"
    else:
        status = "partial" if hits else "missing"
        if hits:
            note = "Mentioned, but not shown in your experience"

    return RequirementResult(
        id=req.id,
        kind=req.kind,
        text=req.text,
        importance=req.importance,
        status=status,
        evidence=quote if verified else "",
        matched_keywords=hits,
        note=note,
    )


def _weighted_pct(results: List[RequirementResult]) -> Optional[int]:
    total = sum(IMPORTANCE[r.importance] for r in results)
    if not total:
        return None
    got = sum(IMPORTANCE[r.importance] * CREDIT[r.status] for r in results)
    return round(100 * got / total)


def _coverage_detail(results: List[RequirementResult]) -> str:
    met = sum(r.status == "met" for r in results)
    partial = sum(r.status == "partial" for r in results)
    s = f"{met} of {len(results)} requirement{'s' * (len(results) != 1)} met"
    return s + (f", {partial} partly" if partial else "")


def _requirement_category(results: List[RequirementResult], kinds: set, none_text: str) -> tuple[int, str]:
    subset = [r for r in results if r.kind in kinds]
    pct = _weighted_pct(subset)
    if pct is None:
        return None, none_text + " — not counted"
    detail = _coverage_detail(subset)
    years = [r.note for r in subset if r.kind == "experience" and "years (asks" in r.note]
    return pct, detail + (f" ({'; '.join(years)})" if years else "")


# ---- Resume-only heuristics ----

def _content_lines(resume: str) -> List[str]:
    """Bullet lines if the resume uses bullets, else any sentence-length line; contact lines excluded."""
    lines = [l.strip() for l in resume.splitlines() if l.strip() and not re.search(r"@|https?://|www\.", l)]
    bullets = [l for l in lines if re.match(r"^[-*•▪◦●‣–]\s*", l)]
    return bullets if len(bullets) >= 3 else [l for l in lines if len(l.split()) >= 6]


def _impact(resume: str) -> tuple[int, str]:
    lines = _content_lines(resume)
    if not lines:
        return 0, "No bullet-style lines found"
    quantified = sum(bool(re.search(r"\d", re.sub(r"\b(19|20)\d{2}\b", "", l))) for l in lines)
    score = min(100, round(100 * quantified / len(lines) / QUANTIFIED_TARGET))
    return score, f"{quantified} of {len(lines)} bullet lines include a number"


CLARITY_CHECKS = [
    ("email address", lambda r: re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", r)),
    ("phone or LinkedIn", lambda r: re.search(r"linkedin\.com|\+?\d[\d\s().-]{7,}\d", r, re.I)),
    ("Experience section", lambda r: re.search(r"^\W*(work |professional )?experience\b", r, re.I | re.M)),
    ("Education section", lambda r: re.search(r"^\W*education\b", r, re.I | re.M)),
    ("Skills section", lambda r: re.search(
        r"^\W*((technical |core |key )?(skills|competencies)|areas? of expertise|expertise|tools\b|technologies)", r, re.I | re.M)),
    ("length of 250-1100 words", lambda r: 250 <= len(r.split()) <= 1100),
    ("no wall-of-text bullets (45+ words) or paragraphs (90+ words)", lambda r: all(
        len(l.split()) < (45 if re.match(r"^[-*•▪◦●‣–]", l.strip()) else 90) for l in r.splitlines())),
]


def _clarity(resume: str) -> tuple[int, str]:
    failed = [name for name, check in CLARITY_CHECKS if not check(resume)]
    score = round(100 * (len(CLARITY_CHECKS) - len(failed)) / len(CLARITY_CHECKS))
    return score, ("Missing: " + ", ".join(failed)) if failed else "All structure checks pass"


# ---- Scorecard ----

@dataclass
class Scorecard:
    overall: int
    standing: str
    categories: List[CategoryScore]  # rationale left blank for Claude to fill
    requirements: List[RequirementResult]
    coverage: Optional[Coverage]
    matched_keywords: List[str]
    missing_keywords: List[str]


def compute(resume: str, job: JobRequirements, resume_ev: ResumeEvidence) -> Scorecard:
    resume_norm = _norm(resume)
    toks = _tokens(resume)
    resume_tokens, vocab = f" {' '.join(toks)} ", set(toks)
    by_id = {e.id: e for e in resume_ev.evidence}
    reqs = sorted(job.requirements, key=lambda r: r.importance != "must")  # musts first, posting order kept
    results = [_assess(r, by_id.get(r.id), resume_norm, resume_tokens, vocab, resume) for r in reqs]

    with_kw = [r for r, req in zip(results, reqs) if req.keywords]
    kw_total = sum(IMPORTANCE[r.importance] for r in with_kw)
    kw_pct = round(100 * sum(IMPORTANCE[r.importance] for r in with_kw if r.matched_keywords) / kw_total) if kw_total else 100
    kw_found = sum(bool(r.matched_keywords) for r in with_kw)

    parts = {
        "Hard skills & tools": _requirement_category(results, {"skill"}, "No specific skills listed in the posting"),
        "Relevant experience & seniority": _requirement_category(results, {"experience", "title"}, "No experience requirements listed"),
        "ATS keyword coverage": (kw_pct, f"{kw_found} of {len(with_kw)} requirements found word-for-word"),
        "Impact & quantified results": _impact(resume),
        "Education & certifications": _requirement_category(results, {"education", "certification"}, "No education or certification required"),
        "Clarity & formatting": _clarity(resume),
    }
    categories = [
        CategoryScore(name=n, score=0 if sc is None else sc, weight=0 if sc is None else WEIGHTS[n], detail=d, rationale="")
        for n, (sc, d) in parts.items()
    ]
    counted = sum(c.weight for c in categories) or 1
    uncapped = round(sum(c.score * c.weight for c in categories) / counted)

    musts = [r for r in results if r.importance == "must"]
    nices = [r for r in results if r.importance == "nice"]
    must_pct = _weighted_pct(musts)
    must_missing = sum(r.status == "missing" for r in musts)
    cap, cap_reason = 100, ""
    if must_pct is not None and must_pct < 50:
        cap, cap_reason = 64, "Capped at 64: under half of the must-have requirements are met"
    elif must_missing:
        cap, cap_reason = 79, f"Capped at 79: {must_missing} must-have requirement{'s' * (must_missing > 1)} missing"
    overall = min(uncapped, cap)
    if overall == uncapped:
        cap_reason = ""

    coverage = Coverage(
        must_total=len(musts),
        must_met=sum(r.status == "met" for r in musts),
        must_partial=sum(r.status == "partial" for r in musts),
        must_pct=100 if must_pct is None else must_pct,
        nice_total=len(nices),
        nice_met=sum(r.status == "met" for r in nices),
        nice_partial=sum(r.status == "partial" for r in nices),
        nice_pct=_weighted_pct(nices) or 0,
        keyword_pct=kw_pct,
        uncapped_score=uncapped,
        cap_reason=cap_reason,
    )

    matched, missing = [], []
    for r, req in zip(results, reqs):
        matched += [k for k in r.matched_keywords if k not in matched]
        if not r.matched_keywords and r.status != "met" and req.keywords and req.keywords[0] not in missing:
            missing.append(req.keywords[0])

    return Scorecard(overall, standing_for(overall), categories, results, coverage, matched[:20], missing[:15])


def assemble(card: Scorecard, narrative: Narrative, candidate_name: str, job_title: str, company: str) -> Analysis:
    rationales = {r.name: r.rationale for r in narrative.rationales}
    categories = [c.model_copy(update={"rationale": rationales.get(c.name, "")}) for c in card.categories]
    return Analysis(
        candidate_name=candidate_name,
        job_title=job_title,
        company=company,
        overall_score=card.overall,
        standing=card.standing,
        verdict=narrative.verdict,
        categories=categories,
        matched_keywords=card.matched_keywords,
        missing_keywords=card.missing_keywords,
        strengths=narrative.strengths,
        gaps=narrative.gaps,
        red_flags=narrative.red_flags,
        changes=narrative.changes,
        requirements=card.requirements,
        coverage=card.coverage,
    )


# ---- Resume-only mode (no job posting) ----

RESUME_WEIGHTS = {
    "ATS keyword coverage": 20,
    "Impact & quantified results": 20,
    "Action-oriented bullets": 15,
    "Clarity & formatting": 15,
    "Skills section": 10,
    "Experience detail": 10,
    "Contact & profile": 10,
}
WEAK_OPENERS = ("responsible for", "worked on", "helped", "assisted", "involved in", "participated in",
                "tasked with", "duties included", "in charge of", "handled", "various", "supported", "worked with")
STRONG_VERBS = {
    "achieved", "built", "created", "cut", "delivered", "designed", "developed", "drove", "grew", "improved",
    "increased", "launched", "led", "managed", "mentored", "negotiated", "owned", "reduced", "ran", "shipped",
    "spearheaded", "streamlined", "won", "architected", "automated", "coached", "directed", "established",
    "expanded", "generated", "implemented", "introduced", "optimized", "orchestrated", "pioneered", "redesigned",
    "scaled", "secured", "transformed", "boosted", "accelerated", "championed", "defined", "headed", "produced",
}
SKILLS_TARGET = 12  # a skills section naming ~12 concrete tools/skills earns full marks


def _bullet_text(line: str) -> str:
    return re.sub(r"^[-*•▪◦●‣–]\s*", "", line).strip()


def _action_bullets(resume: str) -> tuple[int, str]:
    lines = [_bullet_text(l) for l in _content_lines(resume)]
    if not lines:
        return 0, "No bullet-style lines found"
    strong = weak = 0
    for l in lines:
        low = l.lower()
        first = re.sub(r"[^a-z]", "", low.split()[0]) if low.split() else ""
        if low.startswith(WEAK_OPENERS):
            weak += 1
        elif first in STRONG_VERBS or (first.endswith("ed") and len(first) > 4):
            strong += 1
    score = round(100 * strong / len(lines))
    return score, f"{strong} of {len(lines)} lines open with a strong action verb" + (f", {weak} with weak phrasing" if weak else "")


def _skills_section(resume_norm: str, profile: ResumeProfile) -> tuple[int, str]:
    found = keyword_hits(profile.hard_skills, resume_norm)
    score = min(100, round(100 * len(found) / SKILLS_TARGET))
    return score, f"{len(found)} concrete skills/tools named (target {SKILLS_TARGET}+)"


def _experience_detail(profile: ResumeProfile) -> tuple[int, str]:
    if not profile.roles:
        return 0, "No roles found"
    dated = sum(bool(r.start.strip() and r.end.strip()) for r in profile.roles)
    # recent roles carry the weight: grade bullet depth on the 3 most recent (partial credit below 3 bullets)
    recent = profile.roles[:3]
    depth = sum(min(r.bullets, 3) / 3 for r in recent) / len(recent)
    score = round(50 * dated / len(profile.roles) + 50 * depth)
    full = sum(r.bullets >= 3 for r in recent)
    return score, f"{dated} of {len(profile.roles)} roles dated; {full} of {len(recent)} recent roles have 3+ bullets"


CONTACT_CHECKS = [
    ("email", lambda r: re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", r)),
    ("phone", lambda r: re.search(r"\+?\d[\d\s().-]{7,}\d", r)),
    ("LinkedIn URL", lambda r: re.search(r"linkedin\.com/in/", r, re.I)),
    ("summary section", lambda r: re.search(r"^\W*(summary|profile|about( me)?|professional summary|objective)\b", r, re.I | re.M)
        or any(len(l.split()) >= 25 for l in r.splitlines()[:8])),  # an unlabeled intro paragraph counts
]


def _contact(resume: str) -> tuple[int, str]:
    failed = [name for name, check in CONTACT_CHECKS if not check(resume)]
    score = round(100 * (len(CONTACT_CHECKS) - len(failed)) / len(CONTACT_CHECKS))
    return score, ("Missing: " + ", ".join(failed)) if failed else "Email, phone, LinkedIn and summary all present"


def compute_resume(resume: str, profile: ResumeProfile) -> Scorecard:
    resume_norm = _norm(resume)
    seen, expected = set(), []
    for k in profile.expected_keywords:
        if k.term.strip() and k.term.lower() not in seen:
            seen.add(k.term.lower())
            expected.append(k)
    # a keyword counts if the resume uses the term or any common variant of it
    hits = [k.term for k in expected if keyword_hits([k.term, *k.aliases], resume_norm)]
    kw_pct = round(100 * len(hits) / len(expected)) if expected else 0
    parts = {
        "ATS keyword coverage": (kw_pct, f"{len(hits)} of {len(expected)} keywords expected for {profile.target_role or 'this role'} found word-for-word"),
        "Impact & quantified results": _impact(resume),
        "Action-oriented bullets": _action_bullets(resume),
        "Clarity & formatting": _clarity(resume),
        "Skills section": _skills_section(resume_norm, profile),
        "Experience detail": _experience_detail(profile),
        "Contact & profile": _contact(resume),
    }
    categories = [CategoryScore(name=n, score=s, weight=RESUME_WEIGHTS[n], detail=d, rationale="") for n, (s, d) in parts.items()]
    overall = round(sum(c.score * c.weight for c in categories) / 100)
    missing = [k.term for k in expected if k.term not in hits]
    return Scorecard(overall, standing_for(overall), categories, [], None, hits[:20], missing[:15])


def assemble_resume(card: Scorecard, narrative: ResumeNarrative, profile: ResumeProfile) -> Analysis:
    rationales = {r.name: r.rationale for r in narrative.rationales}
    return Analysis(
        candidate_name=profile.candidate_name,
        job_title=profile.target_role,
        company="",
        overall_score=card.overall,
        standing=card.standing,
        verdict=narrative.verdict,
        categories=[c.model_copy(update={"rationale": rationales.get(c.name, "")}) for c in card.categories],
        matched_keywords=card.matched_keywords,
        missing_keywords=card.missing_keywords,
        strengths=narrative.strengths,
        gaps=narrative.gaps,
        red_flags=narrative.red_flags,
        changes=narrative.changes,
        mode="resume",
    )
