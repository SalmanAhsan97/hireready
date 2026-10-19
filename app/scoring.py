"""Deterministic scoring: the same resume text + extraction always yields the same score.

Claude only extracts facts (the job's requirements and the resume's quoted evidence for each).
Everything numeric happens here: evidence is checked against the resume text, keywords are matched
literally like an ATS would, and the weighted overall score is computed from those results.
"""
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Iterable, List, Optional

from .synonyms import variants
from .models import (
    Analysis,
    CategoryScore,
    CompareEntry,
    CompareResult,
    CompareRow,
    Lenses,
    Skim,
    Change,
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


# ---- Dates and experience (computed here: the model doesn't know today's date and can't do date math reliably) ----

_MONTH = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
_DATE = rf"(?:{_MONTH}\s*,?\s*|(?:0?[1-9]|1[0-2])\s*[/.-]\s*)?(?:19|20)\d{{2}}"
DATE_RANGE = re.compile(rf"({_DATE})\s*(?:–|—|-|to|until|till)\s*({_DATE}|present|current|now|today|ongoing)", re.I)
_MONTHS3 = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]


def _ym(text: str, end: bool, today: date) -> int | None:
    """Month index (year*12 + month). Year-only dates are taken as mid-year, so "2019–2021" counts as 2 years."""
    t = text.strip().lower()
    if re.match(r"present|current|now|today|ongoing", t):
        return today.year * 12 + today.month
    y = re.search(r"(?:19|20)\d{2}", t)
    if not y:
        return None
    word = re.match(r"[a-z]{3}", t)
    num = re.match(r"(\d{1,2})\s*[/.-]", t)
    month = _MONTHS3.index(word.group()) + 1 if word and word.group() in _MONTHS3 else int(num.group(1)) if num else (6 if end else 7)
    return int(y.group()) * 12 + month


def role_spans(resume: str, today: date | None = None) -> list[dict]:
    """Every line with a date range: its index, text, start/end month index and whether it's current."""
    today = today or date.today()
    spans = []
    for i, line in enumerate(resume.splitlines()):
        m = DATE_RANGE.search(line)
        if not m:
            continue
        start, stop = _ym(m.group(1), False, today), _ym(m.group(2), True, today)
        if start and stop and stop >= start and not re.search(r"\b(education|university|college|school|bachelor|master|degree|ba|bs|msc|bsc)\b", line, re.I):
            spans.append({"line": i, "text": line.strip(), "start": start, "end": stop,
                          "current": bool(re.search(r"present|current|now|today|ongoing", m.group(2), re.I))})
    return spans


def experience_years(resume: str, today: date | None = None) -> float | None:
    """Total years across dated roles, overlaps merged. None if the resume has no dated roles."""
    spans = sorted((s["start"], s["end"]) for s in role_spans(resume, today))
    if not spans:
        return None
    months, cur_s, cur_e = 0, *spans[0]
    for s, e in spans[1:]:
        if s > cur_e + 1:  # back-to-back roles (Jun -> Jul) are continuous
            months += cur_e - cur_s
            cur_s, cur_e = s, e
        else:
            cur_e = max(cur_e, e)
    months += cur_e - cur_s
    return round(months / 12, 1)


STALE_MONTHS = 7 * 12  # evidence only from roles that ended this long ago counts as partial


def stale_roles(resume: str, today: date | None = None) -> tuple[dict[int, int], str]:
    """({line index: end year} for lines inside roles that ended 7+ years ago, normalised text of everything else)."""
    today = today or date.today()
    lines = resume.splitlines()
    spans = sorted(role_spans(resume, today), key=lambda s: s["line"])
    stale: dict[int, int] = {}
    for i, sp in enumerate(spans):
        if sp["current"] or today.year * 12 + today.month - sp["end"] < STALE_MONTHS:
            continue
        stop = spans[i + 1]["line"] if i + 1 < len(spans) else len(lines)
        for j in range(sp["line"], stop):
            if _HEADING.match(lines[j].strip()) and j > sp["line"]:
                break
            stale[j] = sp["end"] // 12
    return stale, _norm("\n".join(l for j, l in enumerate(lines) if j not in stale))


def durations_block(resume: str, today: date | None = None) -> str:
    """Facts handed to the model so it sums durations instead of doing date math itself."""
    today = today or date.today()
    spans = role_spans(resume, today)
    if not spans:
        return ""
    lines = "\n".join(f"- {s['text'][:120]} -> {round((s['end'] - s['start']) / 12, 1)} years" for s in spans)
    return (f"<dates>\nToday is {today:%B %Y}. Durations computed by code from the resume's dates (use these; don't do "
            f"your own date math):\n{lines}\nTotal, overlaps merged: {experience_years(resume, today)} years\n</dates>")


# ---- Resume sections: where a keyword appears matters (experience > skills list > pasted keyword dump) ----

SECTION_NAMES = {
    "skills": r"(technical |core |key |professional )?(skills|competencies|tools|technologies|tech stack|expertise|areas? of expertise|skills ?(&|and) tools)",
    "experience": r"(work |professional |relevant )?(experience|employment|work history|career history)",
    "summary": r"(professional )?(summary|profile|about( me)?|objective)",
    "education": r"education|academic.*",
    "projects": r"(selected |key )?projects?",
    "other": r"certifications?|licen[cs]es?|awards?|honou?rs|volunteer(ing)?|languages|interests|publications|references|achievements",
}
_HEADING = re.compile(r"^\W*(" + "|".join(f"(?P<{k}>{v})" for k, v in SECTION_NAMES.items()) + r")\s*:?\s*$", re.I)
SKILL_LABEL = re.compile(r"^\W*(" + SECTION_NAMES["skills"] + r")\s*[:\-–]", re.I)
DUMP_LABEL = re.compile(r"^\W*(ats )?(keywords?|key words|tags|search terms)\s*[:\-–]", re.I)


def sections(resume: str) -> list[tuple[str, str]]:
    """(line, section) for every line; text before the first heading is the header/summary area."""
    out, current = [], "summary"
    for line in resume.splitlines():
        m = _HEADING.match(line.strip())
        if m and len(line.split()) <= 5:
            current = next(k for k, v in m.groupdict().items() if v)
            out.append((line, "heading"))
            continue
        out.append((line, "skills" if SKILL_LABEL.match(line) else current))
    return out


def keyword_dump_lines(resume: str) -> list[str]:
    """Lines that only list search terms: labelled "Keywords:" or a 10+ item list outside the skills section."""
    dumps = []
    for line, sec in sections(resume):
        items = [x for x in re.split(r"[,|•;·]", line) if x.strip()]
        long_list = len(items) >= 10 and all(len(x.split()) <= 4 for x in items)
        if DUMP_LABEL.match(line) or (long_list and sec not in ("skills", "heading")):
            dumps.append(line)
    return dumps


def context_and_skills(resume: str) -> tuple[str, str]:
    """Normalised text of the experience/summary/projects lines and of the skills lines, separately."""
    context, skills = [], []
    for line, sec in sections(resume):
        if sec == "heading" or re.search(r"@|https?://|www\.", line):
            continue
        (skills if sec == "skills" else context).append(line)
    return _norm("\n".join(context)), _norm("\n".join(skills))


# ---- Majority vote over several readings of the same resume against the same requirements ----

_RANK = {"missing": 0, "partial": 1, "met": 2}


def merge_evidence(votes: list[ResumeEvidence], resume: str) -> ResumeEvidence:
    """Per requirement: the median verdict of the readings (2 of 3 agree -> theirs; all differ -> partial),
    a quote from a reading with that verdict (one that's really in the resume first), and the median years."""
    if len(votes) == 1:
        return votes[0]
    toks = _tokens(resume)
    resume_tokens, vocab = f" {' '.join(toks)} ", set(toks)
    by_id: dict[str, list[Evidence]] = {}
    for v in votes:
        for e in v.evidence:
            by_id.setdefault(e.id, []).append(e)
    merged = []
    for rid, evs in by_id.items():
        ranks = sorted(_RANK[e.status] for e in evs)
        median = ranks[(len(ranks) - 1) // 2]  # lower median: with an even number of readings, the stricter one wins
        status = next(k for k, r in _RANK.items() if r == median)
        same = [e for e in evs if e.status == status] or evs
        quotes = [e.quote for e in same if e.quote.strip()]
        quote = next((q for q in quotes if quote_found(q, resume_tokens, vocab)), quotes[0] if quotes else "")
        years = sorted(e.years for e in evs if e.years is not None)
        merged.append(Evidence(id=rid, status=status, quote=quote, years=years[(len(years) - 1) // 2] if years else None))
    name = next((v.candidate_name for v in votes if v.candidate_name.strip()), "")
    return ResumeEvidence(candidate_name=name, evidence=merged)


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

def _assess(req: Requirement, ev: Optional[Evidence], resume_norm: str, resume_tokens: str, vocab: set, resume_text: str,
            total_years: float | None = None, stale: tuple[dict[int, int], str] | None = None) -> RequirementResult:
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
    elif req.kind == "experience" and req.min_years and (ev and ev.years is not None or total_years is not None):
        # the model picks which roles count; the total can never exceed what the dates add up to
        years = ev.years if ev and ev.years is not None else total_years
        if total_years is not None:
            years = min(years, total_years)
        ratio = years / req.min_years
        status = "met" if ratio >= 1 else "partial" if ratio >= 0.75 else "missing"
        note = f"{years:g} years (asks for {req.min_years}+)"
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
    # recency: a skill shown only in a role that ended 7+ years ago is weaker evidence
    if status == "met" and verified and stale and stale[0] and req.kind in ("skill", "title"):
        line = _quote_line(quote, resume_text)
        if line in stale[0] and not keyword_hits(req.keywords, stale[1]):
            status, note = "partial", f"Only shown in a role that ended in {stale[0][line]}; recruiters weigh recent experience"
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


def _quote_line(quote: str, resume: str) -> int | None:
    """Index of the resume line the quote comes from (first fragment, token match)."""
    frag = _tokens(re.split(r"\.\.\.|…", quote)[0])[:8]
    if not frag:
        return None
    want = " ".join(frag)
    for i, line in enumerate(resume.splitlines()):
        if want in " ".join(_tokens(line)):
            return i
    return None


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


# ---- Job-title alignment (recruiters read titles first; Workday weights title matches heavily) ----

TITLE_SENIORITY = {"senior", "sr", "lead", "principal", "staff", "head", "chief", "director", "manager"}
TITLE_FILLER = {"junior", "jr", "associate", "intern", "ii", "iii", "iv", "the", "and", "for", "of", "remote", "hybrid"}


def title_alignment(job_title: str, resume: str, today: date | None = None) -> dict | None:
    """Score 100 / 50 / 0 for how well the most recent role (or the headline) names the posting's title."""
    words = _norm(job_title).split()
    core = [w for w in words if len(w) > 1 and w not in TITLE_SENIORITY | TITLE_FILLER]
    if not core:
        return None
    spans = sorted(role_spans(resume, today), key=lambda s: (s["end"], s["start"]), reverse=True)
    recent = _norm(re.sub(DATE_RANGE, " ", spans[0]["text"])) if spans else ""
    head = _norm(" ".join(resume.splitlines()[:6]))
    has = lambda text: sum(w in text.split() for w in core)
    if recent and has(recent) == len(core):
        score, where = 100, "your most recent title"
    elif has(head) == len(core) or (recent and has(recent)):
        score, where = 50, "your headline" if has(head) == len(core) else "partly in your most recent title"
    else:
        score, where = 0, ""
    level = [w for w in words if w in TITLE_SENIORITY - {"manager"}]
    if score == 100:
        note = f"Your most recent title matches \u201c{job_title}\u201d"
    elif score:
        note = f"\u201c{job_title}\u201d appears only in your headline" if where == "your headline" else f"Your most recent title partly matches \u201c{job_title}\u201d"
    else:
        note = f"Your recent titles and headline don't name \u201c{job_title}\u201d"
    if score and level and not any(w in (recent + " " + head).split() for w in level):
        score, note = min(score, 50), note + f", but not the {level[0].title()} level it asks for"
    return {"score": score, "note": note}


def _with_title(part: tuple[int | None, str], title: dict | None) -> tuple[int | None, str]:
    pct, detail = part
    if not title:
        return part
    if pct is None:
        return title["score"], title["note"]
    return round(0.7 * pct + 0.3 * title["score"]), f"{detail}. {title['note']}"


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
    gates: List[str] = field(default_factory=list)  # eligibility to confirm before applying (never scored)
    lenses: Optional[Lenses] = None
    skim: Optional[Skim] = None


def compute(resume: str, job: JobRequirements, resume_ev: ResumeEvidence, today: date | None = None) -> Scorecard:
    resume_norm = _norm(resume)
    toks = _tokens(resume)
    resume_tokens, vocab = f" {' '.join(toks)} ", set(toks)
    by_id = {e.id: e for e in resume_ev.evidence}
    reqs = sorted(job.requirements, key=lambda r: r.importance != "must")  # musts first, posting order kept
    total_years = experience_years(resume, today)
    stale = stale_roles(resume, today)
    results = [_assess(r, by_id.get(r.id), resume_norm, resume_tokens, vocab, resume, total_years, stale) for r in reqs]

    # ATS coverage: full credit for a term used in your experience or summary, half for one only in a skills list
    context_norm, skills_norm = context_and_skills(resume)
    with_kw = [(r, req) for r, req in zip(results, reqs) if req.keywords]
    credit = {r.id: 1.0 if keyword_hits(req.keywords, context_norm) else 0.5 if keyword_hits(req.keywords, skills_norm) else 0.0
              for r, req in with_kw}
    kw_total = sum(IMPORTANCE[r.importance] for r, _ in with_kw)
    kw_pct = round(100 * sum(IMPORTANCE[r.importance] * credit[r.id] for r, _ in with_kw) / kw_total) if kw_total else 100
    kw_found = sum(c == 1.0 for c in credit.values())
    kw_skills_only = sum(c == 0.5 for c in credit.values())
    kw_detail = f"{kw_found} of {len(with_kw)} requirements found word-for-word in your experience" + (
        f", {kw_skills_only} only in your skills list (half credit)" if kw_skills_only else "")
    title = title_alignment(job.job_title, resume, today)

    parts = {
        "Hard skills & tools": _requirement_category(results, {"skill"}, "No specific skills listed in the posting"),
        "Relevant experience & seniority": _with_title(_requirement_category(results, {"experience", "title"}, "No experience requirements listed"), title),
        "ATS keyword coverage": (kw_pct, kw_detail),
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

    strict = [r for r, req in zip(results, reqs) if req.keywords]
    lenses = Lenses(
        strict_found=sum(bool(r.matched_keywords) for r in strict), strict_total=len(strict),
        semantic_pct=round(100 * sum(CREDIT[r.status] for r in results) / len(results)) if results else 0,
        semantic_total=len(results),
        title_score=title["score"] if title else None, title_note=title["note"] if title else "",
    )
    return Scorecard(overall, standing_for(overall), categories, results, coverage, matched[:20], missing[:15], list(job.gates),
                     lenses, skim_check(resume, job.job_title, [req for req in reqs if req.importance == "must" and req.keywords][:3]))


def skim_check(resume: str, job_title: str, top_musts: list) -> Skim:
    """Recruiters skim for ~7 seconds, titles first: does the top third name the role and the top must-haves?"""
    lines = [l for l in resume.splitlines() if l.strip()]
    top = _norm("\n".join(lines[:max(8, len(lines) // 3)]))
    # the level counts here too: a skim for "Senior Product Designer" looks for "Senior"
    core = [w for w in _norm(job_title).split() if len(w) > 1 and w not in TITLE_FILLER | {"manager"} or w == "manager"]
    title_shown = bool(core) and all(w in top.split() for w in core)
    shown, missing = [], []
    for req in top_musts:
        (shown if keyword_hits([*req.keywords, *(v for k in req.keywords for v in variants(k))], top) else missing).append(req.keywords[0])
    bits = [f"{'names' if title_shown else 'does not name'} the {job_title or 'target'} title"] if core else []
    if top_musts:
        bits.append(f"shows {len(shown)} of the top {len(top_musts)} must-haves")
    verdict = "In a 7-second skim, the top of your resume " + " and ".join(bits) + "." if bits else ""
    return Skim(title_shown=title_shown, terms_shown=shown, terms_missing=missing, verdict=verdict)


# ---- What each suggested edit is worth (scoring is deterministic, so re-score the edited resume) ----

def apply_edit(resume: str, change: Change) -> str | None:
    """The resume with one edit applied: replace the quoted line, or add the suggestion if nothing is quoted."""
    current, suggested = change.current.strip(), change.suggested.strip()
    if not suggested:
        return None
    if not current:
        return resume.rstrip() + "\n" + suggested + "\n"
    want = " ".join(_tokens(current))
    lines = resume.splitlines()
    for i, line in enumerate(lines):
        if want and want in " ".join(_tokens(line)):
            bullet = re.match(r"^\s*[-*•▪◦●‣–]\s*", line)
            lines[i] = (bullet.group() if bullet else "") + re.sub(r"^\s*[-*•▪◦●‣–]\s*", "", suggested)
            return "\n".join(lines)
    return None


def edit_gains(resume: str, changes: list[Change], rescore, base: int) -> tuple[list[Change], int | None]:
    """Each change with its own gain, plus the score with all of them applied in order."""
    out, combined = [], resume
    for c in changes:
        edited = apply_edit(resume, c)
        gain = max(0, rescore(edited) - base) if edited is not None else None
        out.append(c.model_copy(update={"gain": gain}))
        combined = apply_edit(combined, c) or combined
    projected = rescore(combined) if combined != resume else None
    return out, (max(base, projected) if projected is not None else None)


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
        gates=card.gates,
        lenses=card.lenses,
        skim=card.skim,
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
