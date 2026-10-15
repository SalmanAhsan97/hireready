from app import demo, scoring
from app.models import (
    Evidence,
    Extraction,
    JobRequirements,
    LinkedInProfile,
    ProfileExperience,
    Requirement,
    ResumeEvidence,
    ResumeProfile,
    RoleKeyword,
)


def demo_extraction():
    e = Extraction.model_validate(demo.DEMO_EXTRACTION)
    return e.job, e.resume


def test_same_facts_give_the_same_score():
    job, ev = demo_extraction()
    first = scoring.compute(demo.DEMO_RESUME, job, ev)
    second = scoring.compute(demo.DEMO_RESUME, job, ev)
    assert first.overall == second.overall
    assert [r.model_dump() for r in first.requirements] == [r.model_dump() for r in second.requirements]
    assert 0 <= first.overall <= 100


def test_claimed_match_without_real_evidence_is_downgraded():
    job = JobRequirements(job_title="Designer", company="Acme", requirements=[
        Requirement(id="r1", kind="skill", text="Storybook", importance="must", keywords=["Storybook"], min_years=None),
    ])
    ev = ResumeEvidence(candidate_name="X", evidence=[
        Evidence(id="r1", status="met", quote="Built the Storybook library used by 40 engineers", years=None),
    ])
    card = scoring.compute(demo.DEMO_RESUME, job, ev)
    assert card.requirements[0].status == "partial"  # the quote isn't in the resume and the keyword isn't either
    assert card.requirements[0].evidence == ""


def test_quote_matching_ignores_punctuation_and_spacing():
    resume = "- Led usability testing for the onboarding redesign with 12 customers."
    toks = scoring._tokens(resume)
    assert scoring.quote_found("led usability testing for the onboarding redesign", f" {' '.join(toks)} ", set(toks))


def test_masters_satisfies_bachelors_requirement():
    resume = "EDUCATION\nMS Business Analytics | FAST-NUCES | 2024\nBachelor’s degree, Economics | FCCU | 2020"
    assert scoring.resume_degree(resume) == 3
    assert min(scoring.degree_levels("Bachelor's degree in Marketing or related field")) == 2
    job = JobRequirements(job_title="Marketing Manager", company="X", requirements=[
        Requirement(id="r1", kind="education", text="Bachelor's degree in Marketing", importance="must", keywords=[], min_years=None),
    ])
    ev = ResumeEvidence(candidate_name="X", evidence=[Evidence(id="r1", status="missing", quote="", years=None)])
    assert scoring.compute(resume, job, ev).requirements[0].status == "met"


def test_ms_office_is_not_a_degree():
    assert scoring.degree_levels("Proficient in MS Office and MS Excel") == []


def test_keyword_matching_handles_split_terms_but_not_distant_words():
    text = scoring._norm("Skilled in Meta & Google Ads and email funnels")
    assert scoring.keyword_hits(["Meta Ads", "Google Ads"], text) == ["Meta Ads", "Google Ads"]
    far = scoring._norm("Meta platform work across many teams and later some ads")
    assert scoring.keyword_hits(["Meta Ads"], far) == []


def test_missing_must_have_caps_the_score():
    job = JobRequirements(job_title="Designer", company="Acme", requirements=[
        Requirement(id="r1", kind="skill", text="Figma", importance="must", keywords=["Figma"], min_years=None),
        Requirement(id="r2", kind="skill", text="WCAG", importance="must", keywords=["WCAG"], min_years=None),
    ])
    ev = ResumeEvidence(candidate_name="X", evidence=[
        Evidence(id="r1", status="met", quote="", years=None),
        Evidence(id="r2", status="missing", quote="", years=None),
    ])
    card = scoring.compute(demo.DEMO_RESUME, job, ev)
    assert card.overall <= 79
    assert card.coverage.must_met == 1 and card.coverage.must_total == 2


def test_categories_the_posting_doesnt_ask_for_are_not_counted():
    job = JobRequirements(job_title="Designer", company="Acme", requirements=[
        Requirement(id="r1", kind="skill", text="Figma", importance="must", keywords=["Figma"], min_years=None),
    ])
    ev = ResumeEvidence(candidate_name="X", evidence=[Evidence(id="r1", status="met", quote="", years=None)])
    edu = next(c for c in scoring.compute(demo.DEMO_RESUME, job, ev).categories if c.name == "Education & certifications")
    assert edu.weight == 0 and "not counted" in edu.detail


def test_resume_only_score_is_deterministic():
    profile = ResumeProfile.model_validate(demo.DEMO_PROFILE)
    a, b = scoring.compute_resume(demo.DEMO_RESUME, profile), scoring.compute_resume(demo.DEMO_RESUME, profile)
    assert a.overall == b.overall
    assert sum(c.weight for c in a.categories) == 100


def test_linkedin_checks_the_source_cant_see_are_skipped():
    profile = LinkedInProfile(
        name="Jane", headline="Marketing Manager | Paid social, SEO and growth for SaaS brands", about="I grow brands. " * 60,
        location="Lahore", experiences=[ProfileExperience(title="Marketing Manager", company="Acme", description="Grew signups 40% " * 10, start="2022", end="Present")],
        skills=["SEO"], skills_total=None, certifications=1, recommendations=None, education=1,
        connections=None, has_photo=None, has_banner=None, has_featured=None, custom_url=True,
    )
    card = scoring.compute_profile(profile, "Marketing Manager", [RoleKeyword(term="SEO", aliases=[])])
    skills = next(c for c in card.categories if c.name == "Skills")
    assert skills.weight == 0  # the PDF export has no skills count, so it isn't scored
    headline = next(c for c in card.categories if c.name == "Headline")
    assert headline.score >= 60  # names the target title and is a good length
