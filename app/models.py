from typing import List, Literal, Optional

from pydantic import BaseModel


class Job(BaseModel):
    url: str = ""
    title: str = ""
    company: str = ""
    location: str = ""
    criteria: List[str] = []
    description: str
    sources: List[str] = []  # where the posting came from, e.g. ["Bright Data", "Apify"]
    fetched_at: str = ""  # ISO date the posting text was read (provenance)


# ---- Claude extraction (input to deterministic scoring) ----

RequirementKind = Literal["skill", "experience", "title", "education", "certification"]
Status = Literal["met", "partial", "missing"]


class Requirement(BaseModel):
    id: str
    kind: RequirementKind
    text: str  # as phrased in the posting
    importance: Literal["must", "nice"]
    keywords: List[str]  # literal terms/synonyms an ATS would search the resume for
    min_years: Optional[int]  # only for experience requirements


class JobRequirements(BaseModel):
    job_title: str
    company: str
    requirements: List[Requirement]
    gates: List[str] = []  # hard eligibility conditions a resume can't prove (work authorisation, onsite, clearance...)


class Evidence(BaseModel):
    id: str  # Requirement.id
    status: Status
    quote: str  # verbatim from the resume, "" if none
    years: Optional[float]  # candidate's years for experience requirements


class ResumeEvidence(BaseModel):
    candidate_name: str
    evidence: List[Evidence]


class Extraction(BaseModel):
    job: JobRequirements
    resume: ResumeEvidence


# ---- Claude narrative (written after scoring) ----

CategoryName = Literal[
    "Hard skills & tools",
    "Relevant experience & seniority",
    "ATS keyword coverage",
    "Impact & quantified results",
    "Education & certifications",
    "Clarity & formatting",
]


class CategoryRationale(BaseModel):
    name: CategoryName
    rationale: str


class Change(BaseModel):
    priority: Literal["high", "medium", "low"]
    section: str
    issue: str
    current: str
    suggested: str
    why: str
    gain: Optional[int] = None  # points this edit adds, computed in code by re-scoring the edited resume


class Narrative(BaseModel):
    verdict: str
    rationales: List[CategoryRationale]
    strengths: List[str]
    gaps: List[str]
    red_flags: List[str]
    changes: List[Change]


# ---- Resume-only mode (no job given): Claude extracts facts, code scores general resume quality ----

class ResumeRole(BaseModel):
    title: str
    company: str
    start: str  # as written, "" if missing
    end: str  # as written ("present" allowed), "" if missing
    bullets: int  # number of bullet points / achievement lines under this role


class ResumeProfile(BaseModel):
    candidate_name: str
    target_role: str  # the role this resume is best positioned for
    seniority: Literal["entry", "mid", "senior", "lead", "executive"]
    hard_skills: List[str]  # tools/skills exactly as written in the resume
    roles: List[ResumeRole]
    expected_keywords: List["RoleKeyword"] = []  # filled from RoleKeywords, generated without seeing the resume


class RoleKeyword(BaseModel):
    term: str
    aliases: List[str]  # common variants an ATS treats as the same thing (e.g. GA4 for Google Analytics)


class RoleKeywords(BaseModel):
    keywords: List[RoleKeyword]


ResumeCategoryName = Literal[
    "ATS keyword coverage",
    "Impact & quantified results",
    "Action-oriented bullets",
    "Clarity & formatting",
    "Skills section",
    "Experience detail",
    "Contact & profile",
]


class ResumeCategoryRationale(BaseModel):
    name: ResumeCategoryName
    rationale: str


class ResumeNarrative(BaseModel):
    verdict: str
    rationales: List[ResumeCategoryRationale]
    strengths: List[str]
    gaps: List[str]
    red_flags: List[str]
    changes: List[Change]


# ---- LinkedIn profile evaluation ----

class ProfileExperience(BaseModel):
    title: str
    company: str
    description: str  # "" if the role has no description
    start: str
    end: str


class LinkedInProfile(BaseModel):
    """Normalized from the Apify scrape or extracted from LinkedIn's 'Save to PDF' export.
    None means "not available from this source", and those checks aren't counted."""
    name: str
    headline: str
    about: str
    location: str
    experiences: List[ProfileExperience]
    skills: List[str]
    skills_total: Optional[int]
    certifications: int
    recommendations: Optional[int]
    education: int
    connections: Optional[int]
    has_photo: Optional[bool]
    has_banner: Optional[bool]
    has_featured: Optional[bool]
    custom_url: Optional[bool]
    source: str = ""  # "LinkedIn (via Apify)" | "LinkedIn PDF export"
    open_to_work: Optional[bool] = None  # public #OpenToWork frame (Apify only); recruiter-only mode is invisible
    has_contact: Optional[bool] = None  # email or website shared in Contact info


class ProfileFromPdf(BaseModel):
    """What can be read from LinkedIn's 'Save to PDF' export."""
    name: str
    headline: str
    about: str
    location: str
    experiences: List[ProfileExperience]
    top_skills: List[str]
    certifications: int
    education: int
    profile_url: str
    contact_email: bool
    contact_websites: int


ProfileCategoryName = Literal[
    "Headline",
    "About section",
    "Keyword coverage",
    "Experience descriptions",
    "Experience titles",
    "Career timeline",
    "Skills",
    "Network",
    "Credibility",
    "Profile completeness",
]


class ProfileCategoryRationale(BaseModel):
    name: ProfileCategoryName
    rationale: str


class TopHalf(BaseModel):
    headline: str  # under 220 characters
    about_opening: str  # the first 2-3 lines of About, what shows before "see more"
    warnings: List[str] = []  # filled in code: clichés, figures or skills the profile doesn't show


class ExperienceRewrite(BaseModel):
    company: str
    title: str
    problem: str
    action: str
    result: str  # a [placeholder] when the profile has no number for it
    skill: str  # the search-map skill this entry proves
    text: str  # ready-to-paste description combining the four
    warnings: List[str] = []  # filled in code


class ProofQuestion(BaseModel):
    term: str
    question: str


class ProfileNarrative(BaseModel):
    verdict: str
    rationales: List[ProfileCategoryRationale]
    strengths: List[str]
    gaps: List[str]
    red_flags: List[str]
    changes: List[Change]
    top_half: List[TopHalf] = []  # 3 headline + About-opening versions
    experience_rewrites: List[ExperienceRewrite] = []
    proof_questions: List[ProofQuestion] = []  # questions for terms the profile claims or lacks, never invented answers


# ---- LinkedIn discoverability (search map from real postings; everything counted in code) ----

class PostingFacts(BaseModel):
    """What one posting asks for, as the model read it. Code does all the counting."""
    index: int  # the posting's number in the prompt
    title: str  # the posting's title in its common searchable form, without seniority or location
    industry: str  # the employer's industry in 1-3 words, "" if unclear
    skills: List[str]  # methods and domain skills, short canonical spelling
    tools: List[str]  # named software, platforms, languages
    outcomes: List[str]  # results the role is measured on, 2-4 words each ("pipeline growth")


class PostingsFacts(BaseModel):
    postings: List[PostingFacts]


TermKind = Literal["title", "skill", "tool", "outcome", "industry"]


class SearchTerm(BaseModel):
    term: str
    kind: TermKind
    aliases: List[str] = []
    count: int = 0  # postings that ask for it
    share: int = 0  # percent of postings
    label: Literal["essential", "useful", "noise", "expected"]  # expected = estimated without postings


class SearchMap(BaseModel):
    role: str
    country: str = ""
    postings: int = 0  # how many real postings the map is built from; 0 = estimated keyword list
    sources: dict[str, int] = {}  # postings per job board, e.g. {"LinkedIn": 12, "Indeed": 15}
    terms: List[SearchTerm] = []


class EvidenceRow(BaseModel):
    term: str
    kind: TermKind
    label: str
    places: List[str]  # Headline / Current title / About / Experience — <company> / Skills
    status: Literal["proven", "claimed", "missing"]  # proven = experience text backs it


class SearchCheck(BaseModel):
    name: str
    query: str
    appears: bool
    strength: Literal["high", "low", "none"]
    reasons: List[str]


class Discoverability(BaseModel):
    search_map: SearchMap
    evidence: List[EvidenceRow] = []
    searches: List[SearchCheck] = []
    target_titles: List[str] = []
    top_half: List[TopHalf] = []
    experience_rewrites: List[ExperienceRewrite] = []
    proof_questions: List[ProofQuestion] = []


class AppearanceCheck(BaseModel):
    on_target: List[str]
    partial: List[str]
    off_target: List[str]
    missing_targets: List[str]
    summary: str


# ---- What the API returns ----

class CategoryScore(BaseModel):
    name: str
    score: int  # 0-100
    weight: int  # percent of overall
    detail: str = ""  # the computed facts behind the score
    rationale: str


class RequirementResult(BaseModel):
    id: str
    kind: RequirementKind
    text: str
    importance: Literal["must", "nice"]
    status: Status
    evidence: str
    matched_keywords: List[str]
    note: str = ""


class Coverage(BaseModel):
    must_total: int
    must_met: int
    must_partial: int
    must_pct: int
    nice_total: int
    nice_met: int
    nice_partial: int
    nice_pct: int
    keyword_pct: int
    uncapped_score: int
    cap_reason: str = ""


class Lenses(BaseModel):
    """The same resume through two kinds of screen."""
    strict_found: int  # requirements whose exact posting term appears (Taleo-style literal search)
    strict_total: int
    semantic_pct: int  # requirements met (1) or partly met (0.5), variants and evidence allowed (Workday-style)
    semantic_total: int
    title_score: Optional[int] = None
    title_note: str = ""


class Skim(BaseModel):
    """What a recruiter sees in the top third of the resume in a ~7-second skim."""
    title_shown: bool
    terms_shown: List[str]
    terms_missing: List[str]
    verdict: str


class Assessment(BaseModel):
    """How solid the analysis is (not interview odds) and what to do about the job. Computed in code."""
    confidence: Literal["high", "medium", "low"]
    confidence_reasons: List[str]
    decision: Literal["apply", "apply_after_edits", "stretch", "deprioritize", "provisional"]
    decision_label: str
    decision_reason: str
    bands: str = ""  # calibrated fit bands behind the standing and decision; "" = the built-in bands


class Analysis(BaseModel):
    candidate_name: str
    job_title: str
    company: str
    overall_score: int  # 0-100
    standing: Literal["long shot", "possible", "competitive", "top candidate"]
    verdict: str
    categories: List[CategoryScore]
    matched_keywords: List[str]
    missing_keywords: List[str]
    strengths: List[str]
    gaps: List[str]
    red_flags: List[str]
    changes: List[Change]
    requirements: List[RequirementResult] = []
    coverage: Optional[Coverage] = None
    mode: Literal["job", "resume", "linkedin"] = "job"  # "resume" = no job posting; "linkedin" = profile review
    warnings: List[str] = []  # problems with the uploaded file itself (e.g. hidden text), found in code
    gates: List[str] = []  # eligibility conditions from the posting to confirm before applying (not scored)
    lenses: Optional[Lenses] = None
    skim: Optional[Skim] = None
    projected_score: Optional[int] = None  # score with every suggested edit applied, computed in code
    assessment: Optional[Assessment] = None
    writing_flags: List[str] = []  # clichés, repeated metrics, repeated work-authorisation lines (advisory, not scored)
    discover: Optional[Discoverability] = None  # LinkedIn mode: search map, evidence, recruiter searches, rewrites
    keyword_basis: str = ""  # set when imported library postings lifted the expected keyword list


class CompareEntry(BaseModel):
    name: str  # file name of the resume
    rank: int  # 1 = best fit
    overall_score: int
    standing: str  # the shortlist-chance tier: top candidate / competitive / possible / long shot
    must_met: int
    must_partial: int
    must_total: int
    keyword_pct: int
    cap_reason: str = ""
    matched_keywords: List[str]
    missing_keywords: List[str]
    unique_strengths: List[str]  # requirements only this resume meets


class CompareRow(BaseModel):
    text: str
    importance: Literal["must", "nice"]
    statuses: List[Status]  # one per entry, in rank order


class CompareResult(BaseModel):
    job_title: str
    company: str
    verdict: str
    tie: bool
    entries: List[CompareEntry]
    rows: List[CompareRow]


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ResumeEdit(BaseModel):
    reply: str  # short message back to the user
    changed: bool  # False when the reply only asks a question or declines
    changes: List[str]  # one line per edit made
    resume_markdown: str  # the full resume after the edits


class TalkingPoint(BaseModel):
    question: str
    answer_outline: str


class ApplyKit(BaseModel):
    positioning_statement: str
    projected_score: int
    tailored_resume_markdown: str
    cover_letter: str
    linkedin_connection_note: str
    recruiter_message: str
    hiring_manager_email_subject: str
    hiring_manager_email_body: str
    who_to_contact: List[str]
    interview_talking_points: List[TalkingPoint]
    application_checklist: List[str]
    warnings: List[str] = []  # figures in the kit that appear in neither the resume nor the job (checked in code)


ResumeProfile.model_rebuild()


# ---- Importer: what the model reads out of a ChatGPT export or handover note. Code validates and stores. ----

class ImportPosting(BaseModel):
    title: str  # the job title as posted
    company: str = ""
    location: str = ""
    market: str = ""  # country, e.g. "Canada"
    posted_on: str = ""  # YYYY-MM-DD or YYYY-MM when known, else ""
    description: str = ""  # the posting text, copied verbatim when it's there; never the candidate's own text
    terms: List[str] = []  # skills and tools the posting asks for, as written


class ImportApplication(BaseModel):
    role: str
    company: str = ""
    applied_on: str = ""
    outcome: Literal["applied", "interview", "offer", "rejected", "no_reply", "unknown"]
    job_text: str = ""  # the full posting the candidate applied with, verbatim, when present
    resume_text: str = ""  # the resume version sent, verbatim, when present


class ImportVaultItem(BaseModel):
    kind: Literal["fact", "figure", "removed", "corrected", "limit"]
    claim: str  # the fact or claim as the owner states it
    figure: str = ""  # the number in it, e.g. "4x" or "35%"
    status: Literal["verified", "unverified", "removed"]
    applies_to: str = ""  # role scope ("Social Media Manager"); "" = every role
    max_mentions: Optional[int] = None  # kind "limit": state it at most this many times
    correction: str = ""  # the corrected wording, for kind "corrected"


class ImportBatch(BaseModel):
    postings: List[ImportPosting] = []
    applications: List[ImportApplication] = []
    vault: List[ImportVaultItem] = []


# ---- Content plan: what similar people post, when it lands, and a calendar built from it ----

PostFormat = Literal["text", "image", "video", "document", "article", "repost"]


class ContentPost(BaseModel):
    id: str
    author: str = ""
    author_url: str = ""
    posted_at: str  # ISO UTC timestamp
    likes: int = 0
    comments: int = 0
    format: PostFormat = "text"
    text: str = ""  # first 500 characters
    url: str = ""
    engaged: bool = False  # written by someone else; the peer reacted to or reposted it


class PostLabel(BaseModel):
    id: str
    topic: str  # 1-4 words, the subject (e.g. "design systems", "career change", "hiring news")
    hook: Literal["question", "story", "number", "list", "opinion", "announcement", "other"]


class PostLabels(BaseModel):
    labels: List[PostLabel]


class TopicHook(BaseModel):
    topic: str
    hook: str  # the post's first line


class TopicHooks(BaseModel):
    hooks: List[TopicHook]


class TimeSlot(BaseModel):
    weekday: str
    hours: str  # "08:00–10:00" in the viewer's time zone
    posts: int
    lift: float  # median engagement here vs each author's usual; 1.0 = usual


class FormatStat(BaseModel):
    format: str
    posts: int
    lift: float


class TopicIdea(BaseModel):
    topic: str
    posts: int = 0
    peers: int = 0  # how many of the peers post about it
    lift: Optional[float] = None  # None = nobody in the peer group posts it yet
    proves: List[str] = []  # recruiter-searched skills it would show
    value: int  # 0-100, computed in code
    example_url: str = ""


class CalendarSlot(BaseModel):
    date: str  # YYYY-MM-DD
    weekday: str
    time: str
    topic: str
    format: str
    value: int
    why: str
    hook: str = ""


class Benchmark(BaseModel):
    peers: int
    authors: int = 0  # everyone whose posts are in the sample: the peers plus people and companies they engaged with
    peer_posts: int  # written by the similar profiles
    engaged_posts: int = 0  # by others, that the similar profiles reacted to or reposted
    peer_per_week: float
    your_posts: int
    your_per_week: Optional[float] = None
    your_last_post_days: Optional[int] = None
    best_format: str = ""
    best_format_lift: Optional[float] = None


class ContentPlan(BaseModel):
    status: Literal["ready", "collecting", "locked", "unavailable"]
    message: str = ""
    role: str = ""
    tz: str = "UTC"
    benchmark: Optional[Benchmark] = None
    slots: List[TimeSlot] = []
    formats: List[FormatStat] = []
    topics: List[TopicIdea] = []
    calendar: List[CalendarSlot] = []
    peers: List[str] = []
    measured: bool = True  # False = too few peer posts; times are general guidance
    preview: bool = False
