"""LinkedIn discoverability: what recruiters search for this role, and whether this profile would turn up.

The model only reads postings (titles, skills, tools, outcomes per posting). Everything here is code:
how many postings ask for each term, which terms are essential, where each term sits on the profile,
the recruiter searches built from the map and whether the profile matches them.
"""
import re

from .models import (
    AppearanceCheck,
    EvidenceRow,
    LinkedInProfile,
    PostingFacts,
    SearchCheck,
    SearchMap,
    SearchTerm,
)
from .scoring import CLICHES, SENIORITY_WORDS, _norm, keyword_hits
from .synonyms import variants

ESSENTIAL_SHARE = 50  # asked for by at least half the postings
USEFUL_SHARE = 20
MIN_POSTINGS = 5  # fewer than this and the counts mean little: fall back to the estimated keyword list
EVIDENCE_KINDS = ("skill", "tool", "outcome")


def _label(share: int) -> str:
    return "essential" if share >= ESSENTIAL_SHARE else "useful" if share >= USEFUL_SHARE else "noise"


def canon(term: str) -> str:
    """One key per skill, so "K8s" and "Kubernetes" (or "SEO" and "seo") count as the same term."""
    group = sorted({_norm(term), *(_norm(v) for v in variants(term))})
    return group[0] if group else ""


def build_search_map(role: str, country: str, postings: list, facts: list[PostingFacts]) -> SearchMap:
    """postings: objects with .title and .description; facts: the model's reading of them, by index (1-based).
    A posting counts for a term if the model listed it for that posting or the term appears in its text."""
    n = len(postings)
    texts = [_norm(f"{p.title} {p.description}") for p in postings]
    titles = [_norm(p.title) for p in postings]
    by_index = {f.index: f for f in facts if 1 <= f.index <= n}
    # candidate vocabulary: every term the model saw, keyed by canonical form; spelling and kind by majority
    seen: dict[str, dict] = {}
    listed: dict[str, set[int]] = {}
    for i, f in by_index.items():
        for kind, terms in (("title", [f.title]), ("industry", [f.industry]), ("skill", f.skills),
                            ("tool", f.tools), ("outcome", f.outcomes)):
            for t in terms:
                t = " ".join(t.split())[:60]
                key = canon(t)
                if not key or len(key) < 2:
                    continue
                entry = seen.setdefault(key, {"spell": {}, "kind": {}})
                entry["spell"][t] = entry["spell"].get(t, 0) + 1
                entry["kind"][kind] = entry["kind"].get(kind, 0) + 1
                listed.setdefault(key, set()).add(i)
    out = []
    for key, entry in seen.items():
        term = max(entry["spell"], key=lambda s: (entry["spell"][s], s))
        kind = max(entry["kind"], key=lambda k: (entry["kind"][k], k))
        names = [term, *variants(term)]
        hay = titles if kind == "title" else texts
        hit = {i + 1 for i, text in enumerate(hay) if keyword_hits(names, text)} | listed[key]
        if kind == "industry":
            hit = listed[key]  # the industry is a reading of the employer, rarely a literal phrase in the text
        share = round(100 * len(hit) / n) if n else 0
        out.append(SearchTerm(term=term, kind=kind, aliases=variants(term), count=len(hit), share=share, label=_label(share)))
    out.sort(key=lambda t: (-t.count, t.kind, t.term.lower()))
    return SearchMap(role=role, country=country, postings=n, terms=out)


def estimated_map(role: str, expected: list) -> SearchMap:
    """Fallback when no postings could be read: the model's keyword list for the role, unweighted."""
    terms = [SearchTerm(term=k.term, kind="skill", aliases=k.aliases, label="expected") for k in expected if k.term.strip()]
    return SearchMap(role=role, postings=0, terms=terms)


def scored_terms(smap: SearchMap) -> list[tuple[SearchTerm, int]]:
    """Terms that count toward keyword coverage, with weights: essential 2, useful 1; noise left out."""
    weight = {"essential": 2, "useful": 1, "expected": 1}
    return [(t, weight[t.label]) for t in smap.terms if t.kind in ("skill", "tool") and t.label in weight]


def _names(t: SearchTerm) -> list[str]:
    return list(dict.fromkeys([t.term, *t.aliases, *variants(t.term)]))


def profile_fields(profile: LinkedInProfile) -> list[tuple[str, str]]:
    """(place, normalized text) in the order recruiters weigh them."""
    fields = [("Headline", profile.headline)]
    if profile.experiences:
        fields.append(("Current title", profile.experiences[0].title))
    fields.append(("About", profile.about))
    for e in profile.experiences:
        fields.append((f"Experience — {e.company or e.title}", f"{e.title} {e.description}"))
    fields.append(("Skills", " , ".join(profile.skills)))
    return [(place, _norm(text)) for place, text in fields if text.strip()]


def places(names: list[str], fields: list[tuple[str, str]]) -> list[str]:
    return list(dict.fromkeys(p for p, text in fields if keyword_hits(names, text)))


def evidence_map(profile: LinkedInProfile, smap: SearchMap, limit: int = 20) -> list[EvidenceRow]:
    """For each essential/useful skill, tool and outcome: where it is on the profile, and whether experience backs it."""
    fields = profile_fields(profile)
    rows = []
    for t in smap.terms:
        if t.kind not in EVIDENCE_KINDS or t.label == "noise":
            continue
        where = places(_names(t), fields)
        status = "proven" if any(p.startswith("Experience") for p in where) else "claimed" if where else "missing"
        rows.append(EvidenceRow(term=t.term, kind=t.kind, label=t.label, places=where, status=status))
    rank = {"essential": 0, "useful": 1, "expected": 1}
    rows.sort(key=lambda r: rank.get(r.label, 2))  # stable: keeps the map's frequency order within a label
    return rows[:limit]


def target_titles(role: str, smap: SearchMap, limit: int = 4) -> list[str]:
    """The role plus the title variants postings use (asked for by at least the useful share)."""
    out = [role] if role.strip() else []
    for t in smap.terms:
        if t.kind == "title" and t.label in ("essential", "useful") and canon(t.term) not in {canon(x) for x in out}:
            out.append(t.term)
    return out[:limit]


# ---- Recruiter searches ----

STRONG_TITLE = ("Headline", "Current title")
STRONG_SKILL = ("Headline", "Current title", "Skills")


def recruiter_searches(role: str, smap: SearchMap) -> list[tuple[str, list[str], list[str], str]]:
    """Up to 5 realistic searches: (name, title parts, keyword parts, operator for titles).
    Built only from the map, so the same map always gives the same searches."""
    titles = target_titles(role, smap)
    ranked = [t for t in smap.terms if t.kind in ("skill", "tool") and t.label in ("essential", "useful", "expected")]
    skills = [t.term for t in ranked]
    industry = next((t.term for t in smap.terms if t.kind == "industry" and t.label != "noise"), "")
    primary = titles[0] if titles else ""
    out = []
    if primary and skills:
        out.append(("Title + top skills", [primary], skills[:2], "AND"))
    if len(titles) > 1 and skills:
        out.append(("Title variant + top skill", [titles[1]], skills[:1], "AND"))
    elif primary and len(skills) > 2:
        out.append(("Title + next skills", [primary], skills[2:4], "AND"))
    if primary and industry:
        out.append(("Title + industry", [primary], [industry], "AND"))
    if len(skills) >= 2:
        out.append(("Skills only", [], skills[:3], "AND"))
    if len(titles) > 1:
        out.append(("Any title variant", titles[:3], [], "OR"))
    elif primary:
        out.append(("Title only", [primary], [], "OR"))
    return out[:5]


def _query(titles: list[str], keywords: list[str], op: str) -> str:
    t = f" {op} ".join(f'"{x}"' for x in titles)
    if len(titles) > 1 and keywords:
        t = f"({t})"
    k = " AND ".join(f'"{x}"' if " " in x else x for x in keywords)
    return " AND ".join(p for p in (t, k) if p)


def _term_names(term: str, smap: SearchMap) -> list[str]:
    hit = next((t for t in smap.terms if canon(t.term) == canon(term)), None)
    return _names(hit) if hit else [term, *variants(term)]


def run_search(name: str, titles: list[str], keywords: list[str], op: str, fields: list[tuple[str, str]], smap: SearchMap) -> SearchCheck:
    """Literal/variant match like LinkedIn's keyword search: every AND part must be somewhere on the profile.
    A part found in the headline or current title (or Skills, for skills) ranks the profile higher."""
    reasons, found_all, strong_all = [], True, True
    title_hits = []
    for t in titles:
        where = places(_term_names(t, smap), fields)
        title_hits.append((t, where))
    if titles:
        matched = [(t, w) for t, w in title_hits if w]
        ok = bool(matched) if op == "OR" else len(matched) == len(titles)
        found_all &= ok
        strong_all &= any(set(w) & set(STRONG_TITLE) for _, w in matched) if op == "OR" else all(set(w) & set(STRONG_TITLE) for _, w in matched)
        for t, w in title_hits:
            reasons.append(f'"{t}" in {", ".join(w[:3])}' if w else f'"{t}" isn’t on your profile')
    for k in keywords:
        where = places(_term_names(k, smap), fields)
        found_all &= bool(where)
        strong_all &= bool(set(where) & set(STRONG_SKILL))
        reasons.append(f'"{k}" in {", ".join(where[:3])}' if where else f'"{k}" isn’t on your profile')
    strength = "none" if not found_all else "high" if strong_all else "low"
    return SearchCheck(name=name, query=_query(titles, keywords, op), appears=found_all, strength=strength, reasons=reasons)


def discoverability(profile: LinkedInProfile, role: str, smap: SearchMap) -> list[SearchCheck]:
    fields = profile_fields(profile)
    return [run_search(n, t, k, op, fields, smap) for n, t, k, op in recruiter_searches(role, smap)]


# ---- Guards on the model's rewrites ----

def rewrite_warnings(text: str, known: str, missing_terms: list[str]) -> list[str]:
    """Clichés, figures not on the profile (ai.new_figures) and search terms the profile doesn't show at all."""
    from .ai import new_figures
    low = text.lower()
    out = []
    cliches = [c for c in CLICHES if re.search(rf"(?<![a-z]){re.escape(c)}(?![a-z])", low)]
    if cliches:
        out.append("Uses generic phrases: " + ", ".join(cliches[:3]))
    figures = new_figures(text, known)
    if figures:
        out.append("Check these figures, they aren't on your profile: " + ", ".join(figures[:5]))
    norm = _norm(text)
    unproven = [t for t in missing_terms if keyword_hits([t, *variants(t)], norm)]
    if unproven:
        out.append("Names skills your profile doesn't show yet: " + ", ".join(unproven[:4]) + ". Keep only what you can back up.")
    return out


# ---- "Search appearances" comparison ----

# Sharing only one of these ("Graphic Designer" vs "Product Designer") doesn't make two titles close
ROLE_NOUNS = {"manager", "designer", "engineer", "developer", "analyst", "specialist", "coordinator", "consultant",
              "executive", "officer", "assistant", "director", "administrator", "associate", "representative", "lead"}


def _core(title: str) -> set[str]:
    return {w for w in _norm(title).split() if len(w) > 1 and w not in SENIORITY_WORDS}


def split_titles(pasted: str, limit: int = 30) -> list[str]:
    parts = re.split(r"[\n,;•|·]+", pasted)
    return list(dict.fromkeys(" ".join(p.split()).strip(" -–—:.") for p in parts if p.strip(" -–—:.")))[:limit]


def compare_appearances(pasted: str, targets: list[str]) -> AppearanceCheck:
    """Titles LinkedIn says the profile is found for vs the titles being targeted. A pasted title is on target if
    it contains every core word of a target ("Senior Marketing Manager" for "Marketing Manager"), partial if it
    shares a field word ("Marketing Coordinator"), off target otherwise."""
    found = split_titles(pasted)
    goals = [t for t in targets if _core(t)]
    on, part, off, covered = [], [], [], set()
    for f in found:
        fc = _core(f)
        full = [g for g in goals if _core(g) <= fc]
        some = [g for g in goals if (fc & _core(g)) - ROLE_NOUNS]
        if full:
            on.append(f); covered.update(full)
        elif some:
            part.append(f)
        else:
            off.append(f)
    missing = [g for g in goals if g not in covered]
    if not found:
        summary = "Paste the job titles from LinkedIn's Search appearances to compare them."
    elif not on:
        summary = f"None of the {len(found)} titles you're found for match your target. Put the target title in your headline and current title."
    else:
        summary = f"{len(on)} of {len(found)} titles you're found for match your target" + \
            (f"; {len(off)} are off target." if off else ".")
    return AppearanceCheck(on_target=on, partial=part, off_target=off, missing_targets=missing, summary=summary)
