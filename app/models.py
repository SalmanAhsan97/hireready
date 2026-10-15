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
    expected_keywords: List[str]  # terms recruiters and ATS expect for target_role
    roles: List[ResumeRole]


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
    mode: Literal["job", "resume"] = "job"  # "resume" = scored without a job posting


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
