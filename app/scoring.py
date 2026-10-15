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
)

WEIGHTS = {
    "Hard skills & tools": 30,
    "Relevant experience & seniority": 25,
    "ATS keyword coverage": 15,
    "Impact & quantified results": 15,
    "Education & certifications": 5,
    "Clarity & formatting": 10,
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
        if re.search(rf"(?<![a-z0-9]){re.escape(k)}(?:s|es)?(?![a-z0-9])", resume_norm) and kw not in hits:
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


# ---- Requirement coverage ----

def _assess(req: Requirement, ev: Optional[Evidence], resume_norm: str, resume_tokens: str, vocab: set) -> RequirementResult:
    hits = keyword_hits(req.keywords, resume_norm)
    quote = (ev.quote if ev else "").strip()
    verified = bool(quote) and quote_found(quote, resume_tokens, vocab)
    claimed = ev.status if ev else "missing"
    note = ""

    if req.kind == "experience" and req.min_years and ev and ev.years is not None:
        ratio = ev.years / req.min_years
        status = "met" if ratio >= 1 else "partial" if ratio >= 0.75 else "missing"
        note = f"{ev.years:g} of {req.min_years} years"
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
        return 100, none_text
    detail = _coverage_detail(subset)
    years = [r.note for r in subset if r.kind == "experience" and "years" in r.note]
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
    ("Skills section", lambda r: re.search(r"^\W*(technical |core )?skills\b", r, re.I | re.M)),
    ("length of 250-1100 words", lambda r: 250 <= len(r.split()) <= 1100),
    ("no wall-of-text lines (40+ words)", lambda r: all(len(l.split()) < 40 for l in r.splitlines())),
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
    coverage: Coverage
    matched_keywords: List[str]
    missing_keywords: List[str]


def compute(resume: str, job: JobRequirements, resume_ev: ResumeEvidence) -> Scorecard:
    resume_norm = _norm(resume)
    toks = _tokens(resume)
    resume_tokens, vocab = f" {' '.join(toks)} ", set(toks)
    by_id = {e.id: e for e in resume_ev.evidence}
    reqs = sorted(job.requirements, key=lambda r: r.importance != "must")  # musts first, posting order kept
    results = [_assess(r, by_id.get(r.id), resume_norm, resume_tokens, vocab) for r in reqs]

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
    categories = [CategoryScore(name=n, score=s, weight=WEIGHTS[n], detail=d, rationale="") for n, (s, d) in parts.items()]
    uncapped = round(sum(c.score * c.weight for c in categories) / 100)

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
