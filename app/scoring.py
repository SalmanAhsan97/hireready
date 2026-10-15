"""Deterministic scoring: the same resume text + extraction always yields the same score.

Claude only extracts facts (the job's requirements and the resume's quoted evidence for each).
Everything numeric happens here: evidence is checked against the resume text, keywords are matched
literally like an ATS would, and the weighted overall score is computed from those results.
"""
import re
from dataclasses import dataclass
from datetime import date
from typing import Iterable, List, Optional

from .synonyms import variants
from .models import (
    Analysis,
    CategoryScore,
    CompareEntry,
    CompareResult,
    CompareRow,
    Coverage,
    Evidence,
    JobRequirements,
    Narrative,
    Requirement,
    RequirementResult,
    ResumeEvidence,
    LinkedInProfile,
    ProfileNarrative,
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


def alias_hits(keywords: Iterable[str], resume_norm: str) -> List[tuple[str, str]]:
    """(keyword, name used) for keywords that appear only under another name, like ("Kubernetes", "k8s")."""
    found = []
    for kw in keywords:
        alt = keyword_hits(variants(kw), resume_norm)
        if alt and not keyword_hits([kw], resume_norm):
            found.append((kw, alt[0]))
    return found


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
    # another name for the skill still shows it; ATS keyword coverage below stays literal
    aliased = [] if hits else alias_hits(req.keywords, resume_norm)
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
        status = "met" if verified or hits or aliased else "partial"
        if status == "partial":
            note = "Claimed, but no supporting text found in the resume"
    elif claimed == "partial":
        status = "partial" if verified or hits or aliased else "missing"
    else:
        status = "partial" if hits or aliased else "missing"
        if hits or aliased:
            note = "Mentioned, but not shown in your experience"
    if aliased and not note:
        # exact-match ATS (Taleo, Workday's parser) search the posting's wording literally
        kw, used = aliased[0]
        note = f"Found as \"{used}\"; add \"{kw}\" word-for-word for strict ATS searches"

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
        literal_gap = r.status != "met" or alias_hits(req.keywords, resume_norm)  # found only as "k8s"
        if not r.matched_keywords and literal_gap and req.keywords and req.keywords[0] not in missing:
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


# ---- Comparing resumes for one job (same requirements for every resume, so the scores are comparable) ----

def compare(job: JobRequirements, scored: list[tuple[str, Scorecard]]) -> CompareResult:
    def strength(item):
        name, card = item
        cov = card.coverage
        return (-card.overall, -(cov.must_pct if cov else 0), -(cov.keyword_pct if cov else 0), name.lower())

    ranked = sorted(scored, key=strength)
    met_by = {}
    for name, card in ranked:
        for r in card.requirements:
            if r.status == "met":
                met_by.setdefault(r.id, set()).add(name)
    entries = []
    for i, (name, card) in enumerate(ranked, 1):
        cov = card.coverage
        unique = [r.text for r in card.requirements if met_by.get(r.id) == {name}]
        entries.append(CompareEntry(
            name=name, rank=i, overall_score=card.overall, standing=card.standing,
            must_met=cov.must_met, must_partial=cov.must_partial, must_total=cov.must_total, keyword_pct=cov.keyword_pct,
            cap_reason=cov.cap_reason, matched_keywords=card.matched_keywords[:12], missing_keywords=card.missing_keywords[:8],
            unique_strengths=unique[:6],
        ))
    first = ranked[0][1]
    by_id = [{r.id: r.status for r in card.requirements} for _, card in ranked]
    rows = [CompareRow(text=r.text, importance=r.importance, statuses=[ids.get(r.id, "missing") for ids in by_id])
            for r in first.requirements]

    best, runner = entries[0], entries[1]
    tie = strength(ranked[0])[:3] == strength(ranked[1])[:3]
    if tie:
        verdict = (f"{best.name} and {runner.name} are an even match for this job ({best.overall_score}/100, "
                   f"{best.must_met} of {best.must_total} must-haves each). Pick on the requirements below.")
    else:
        gap = best.overall_score - runner.overall_score
        why = [f"{best.must_met} of {best.must_total} must-haves met (vs {runner.must_met})"] if best.must_met != runner.must_met else []
        if best.keyword_pct != runner.keyword_pct:
            why.append(f"{best.keyword_pct}% of the posting's keywords word-for-word (vs {runner.keyword_pct}%)")
        verdict = (f"{best.name} is the stronger fit: {best.overall_score}/100 vs {runner.overall_score}/100"
                   + (f" (+{gap})" if gap else "") + (f", with {' and '.join(why)}" if why else "") + ".")
    return CompareResult(job_title=job.job_title, company=job.company, verdict=verdict, tie=tie, entries=entries, rows=rows)


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
    hits = [k.term for k in expected if keyword_hits([k.term, *k.aliases, *variants(k.term)], resume_norm)]
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


# ---- LinkedIn profile evaluation ----
# Checks follow LinkedIn's own profile guidance and common recruiter-search practice: a keyword-rich
# headline, a substantial About, described roles with results, many listed skills, social proof.
# Titles, timeline and network tiers borrow from the linkedin-profile-score rubric (henriquesantanati, MIT),
# scored here from the profile's facts instead of by a model.

PROFILE_WEIGHTS = {
    "Headline": 18,
    "About section": 12,
    "Keyword coverage": 18,
    "Experience descriptions": 16,
    "Experience titles": 8,
    "Career timeline": 5,
    "Skills": 8,
    "Network": 6,
    "Credibility": 5,
    "Profile completeness": 4,
}
SENIORITY_WORDS = {"senior", "sr", "junior", "jr", "lead", "principal", "staff", "head", "chief", "associate",
                   "intern", "ii", "iii", "iv", "the", "and", "for"}
CREATIVE_TITLES = r"\b(ninja|wizard|rock ?star|guru|jedi|hero|unicorn|evangelist|magician|hacker)\b"
SHORT_STINT_EXEMPT = r"\b(intern|internship|trainee|apprentice|volunteer|freelance|contract|contractor|fellow)\b"
MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
# LinkedIn shows exact counts up to 500; the tiers follow the linkedin-profile-score rubric
CONNECTION_TIERS = [(500, 100), (300, 80), (150, 60), (50, 40), (0, 20)]


def _has_number(text: str) -> bool:
    return bool(re.search(r"\d", re.sub(r"\b(19|20)\d{2}\b", "", text)))


def _title_match(title: str, core: list[str]) -> float:
    words = set(_norm(title).split())
    hit = sum(w in words for w in core)
    return 1.0 if hit == len(core) else 0.5 if hit else 0.0


def _titles(profile: LinkedInProfile, target_role: str) -> tuple[int | None, str]:
    core = [w for w in _norm(target_role).split() if len(w) > 1 and w not in SENIORITY_WORDS]
    recent = [e for e in profile.experiences if e.title.strip()][:3]
    if not recent or not core:
        return None, "No titles to compare — not counted"
    marks = [_title_match(e.title, core) for e in recent]
    creative = [e.title for e in profile.experiences if re.search(CREATIVE_TITLES, e.title, re.I)]
    score = max(0, round(100 * sum(marks) / len(marks)) - 25 * bool(creative))
    full, part = marks.count(1.0), marks.count(0.5)
    detail = f"{full} of {len(recent)} recent titles name {target_role}" + (f", {part} partly" if part else "")
    return score, detail + (f"; informal title: {creative[0]}" if creative else "")


def _month_index(text: str, end: bool, today: date) -> tuple[int | None, bool]:
    """(year*12 + month, has_month). Year-only dates assume the generous end (Jan start, Dec end)."""
    t = text.strip().lower()
    if not t or re.match(r"present|current|now", t):
        return (today.year * 12 + today.month, True) if t else (None, False)
    year = re.search(r"\b(19|20)\d{2}\b", t)
    if not year:
        return None, False
    mon = next((MONTHS[m[:3]] for m in re.findall(r"[a-z]{3,}", t) if m[:3] in MONTHS), None)
    if mon is None and (num := re.search(r"\b(19|20)\d{2}[-/](\d{1,2})\b|\b(\d{1,2})[-/](19|20)\d{2}\b", t)):
        mon = int(num.group(2) or num.group(3))
    return int(year.group()) * 12 + (mon or (12 if end else 1)), mon is not None


def _timeline(profile: LinkedInProfile, today: date) -> tuple[int | None, str]:
    roles = []
    for e in profile.experiences:
        start, start_m = _month_index(e.start, False, today)
        stop, _ = _month_index(e.end or "present", True, today)
        if start is not None and stop is not None and stop >= start:
            current = not e.end.strip() or bool(re.match(r"present|current|now", e.end.strip(), re.I))
            roles.append((start, stop, start_m, current, e.title))
    if not roles:
        return None, "No dated roles — not counted"
    # stints: the 5 most recent finished roles, ignoring roles that are short by design
    finished = [r for r in sorted(roles, reverse=True) if not r[3] and not re.search(SHORT_STINT_EXEMPT, r[4], re.I)][:5]
    short = sum(stop - start + 1 < 12 for start, stop, *_ in finished)
    stints = 50 * (1 - short / len(finished)) if finished else 50
    # gaps: more than 3 months between the end of everything so far and the next role's start
    gaps, reach = 0, None
    for start, stop, *_ in sorted(roles):
        if reach is not None and start - reach - 1 > 3:
            gaps += 1
        reach = stop if reach is None else max(reach, stop)
    monthly = sum(r[2] for r in roles)
    score = round(stints + (25 if not gaps else 12 if gaps == 1 else 0) + 25 * monthly / len(roles))
    detail = (f"{short} of {len(finished)} recent finished roles under a year; " if finished else "") + \
        f"{gaps} gap{'s' * (gaps != 1)} over 3 months; {monthly} of {len(roles)} roles dated by month"
    return score, detail


def _network(connections: int | None) -> tuple[int | None, str]:
    if connections is None:
        return None, "Connection count not in the PDF export — not counted"
    score = next(pts for floor, pts in CONNECTION_TIERS if connections >= floor)
    return score, f"{'500+' if connections >= 500 else connections} connections (500+ is the credibility threshold)"


def _name_ok(name: str) -> bool:
    letters = re.sub(r"[^A-Za-z]", "", name)
    return bool(name.strip()) and not re.search(r"[^\w\s.,'’()-]", name) and not (len(letters) > 3 and (letters.isupper() or letters.islower()))


def compute_profile(profile: LinkedInProfile, target_role: str, expected: list, today: date | None = None) -> Scorecard:
    today = today or date.today()
    role_norm, head_norm = _norm(target_role), _norm(profile.headline)
    all_text = _norm(" ".join([profile.headline, profile.about, *(f"{e.title} {e.description}" for e in profile.experiences), *profile.skills]))
    terms = [k for k in expected if k.term.strip()]
    names = {k.term: [k.term, *k.aliases, *variants(k.term)] for k in terms}
    hits = [k.term for k in terms if keyword_hits(names[k.term], all_text)]
    head_hits = [k.term for k in terms if keyword_hits(names[k.term], head_norm)]
    parts: dict[str, tuple[int | None, str]] = {}

    role_words = [w for w in role_norm.split() if len(w) > 2]
    has_role = bool(role_words) and all(w in head_norm.split() for w in role_words)
    length_ok = 50 <= len(profile.headline) <= 220
    parts["Headline"] = (
        (40 if has_role else 0) + round(40 * min(1, len(head_hits) / 2)) + (20 if length_ok else 0),
        f"{'Names' if has_role else 'Does not name'} {target_role}; {len(head_hits)} target keywords; {len(profile.headline)} characters (50-220 is ideal)",
    )

    words = len(profile.about.split())
    about_kw = [k.term for k in terms if keyword_hits(names[k.term], _norm(profile.about))]
    parts["About section"] = (
        round(60 * min(1, words / 200)) + round(25 * min(1, len(about_kw) / 3)) + (15 if _has_number(profile.about) else 0),
        f"{words} words (200+ is ideal); {len(about_kw)} target keywords; {'has' if _has_number(profile.about) else 'no'} measurable results",
    )

    kw_pct = round(100 * len(hits) / len(terms)) if terms else 0
    parts["Keyword coverage"] = (kw_pct, f"{len(hits)} of {len(terms)} keywords recruiters search for {target_role} appear on your profile")

    recent = profile.experiences[:3]
    if recent:
        described = sum(len(e.description.split()) >= 25 for e in recent)
        quantified = sum(_has_number(e.description) for e in recent)
        parts["Experience descriptions"] = (
            round(60 * described / len(recent) + 40 * quantified / len(recent)),
            f"{described} of {len(recent)} recent roles described (25+ words); {quantified} include numbers",
        )
    else:
        parts["Experience descriptions"] = (0, "No roles listed")

    parts["Experience titles"] = _titles(profile, target_role)
    parts["Career timeline"] = _timeline(profile, today)

    if profile.skills_total is None:
        parts["Skills"] = (None, "Skills count not in the PDF export — not counted")
    else:
        parts["Skills"] = (min(100, round(100 * profile.skills_total / 30)), f"{profile.skills_total} skills listed (30+ is ideal; LinkedIn allows 100)")

    cred, notes = 0, []
    if profile.recommendations is not None:
        cred += round(40 * min(1, profile.recommendations / 3)); notes.append(f"{profile.recommendations} recommendations")
    cred += 30 if profile.certifications else 0; notes.append(f"{profile.certifications} certifications")
    cred += 30 if profile.education else 0; notes.append(f"{profile.education} education entries")
    max_cred = 100 if profile.recommendations is not None else 60
    parts["Network"] = _network(profile.connections)
    parts["Credibility"] = (round(100 * cred / max_cred), ", ".join(notes))

    checks = {
        "photo": profile.has_photo, "banner": profile.has_banner, "Featured section": profile.has_featured,
        "custom URL": profile.custom_url, "location": bool(profile.location),
        "email or website in Contact info": profile.has_contact, "name in normal capitalization": _name_ok(profile.name),
    }
    known = {k: v for k, v in checks.items() if v is not None}
    missing = [k for k, v in known.items() if not v]
    # The public #OpenToWork frame is reported, not scored: "off" may just mean the recruiter-only setting is on.
    otw = "" if profile.open_to_work is None else f"; Open to Work frame {'on' if profile.open_to_work else 'off'} (not scored)"
    parts["Profile completeness"] = (
        round(100 * (len(known) - len(missing)) / len(known)) if known else None,
        (("Missing: " + ", ".join(missing)) if missing else f"All {len(known)} checks pass") + otw,
    )

    categories = [
        CategoryScore(name=n, score=0 if sc is None else sc, weight=0 if sc is None else PROFILE_WEIGHTS[n], detail=d, rationale="")
        for n, (sc, d) in parts.items()
    ]
    counted = sum(c.weight for c in categories) or 1
    overall = round(sum(c.score * c.weight for c in categories) / counted)
    return Scorecard(overall, standing_for(overall), categories, [], None, hits[:20], [k.term for k in terms if k.term not in hits][:15])


def assemble_profile(card: Scorecard, narrative: ProfileNarrative, profile: LinkedInProfile, target_role: str) -> Analysis:
    rationales = {r.name: r.rationale for r in narrative.rationales}
    return Analysis(
        candidate_name=profile.name,
        job_title=target_role,
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
        mode="linkedin",
    )
