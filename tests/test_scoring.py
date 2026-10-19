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


def _profile(**kw):
    base = dict(
        name="Jane Doe", headline="Marketing Manager | Paid social, SEO and growth for SaaS brands", about="I grow brands. " * 60,
        location="Lahore", experiences=[], skills=["SEO"], skills_total=30, certifications=1, recommendations=2, education=1,
        connections=600, has_photo=True, has_banner=True, has_featured=True, custom_url=True,
    )
    return LinkedInProfile(**{**base, **kw})


def _cat(card, name):
    return next(c for c in card.categories if c.name == name)


def test_profile_weights_sum_to_100():
    assert sum(scoring.PROFILE_WEIGHTS.values()) == 100


def test_titles_matching_the_target_score_higher():
    exp = lambda t: ProfileExperience(title=t, company="Acme", description="", start="Jan 2020", end="Dec 2021")
    aligned = scoring.compute_profile(_profile(experiences=[exp("Senior Marketing Manager"), exp("Marketing Manager")]), "Marketing Manager", [])
    mixed = scoring.compute_profile(_profile(experiences=[exp("Growth Ninja"), exp("Sales Executive")]), "Marketing Manager", [])
    assert _cat(aligned, "Experience titles").score == 100
    assert _cat(mixed, "Experience titles").score == 0 and "Ninja" in _cat(mixed, "Experience titles").detail


def test_job_hopping_and_gaps_lower_the_timeline_score():
    today = scoring.date(2026, 10, 1)
    exp = lambda s, e, t="Marketing Manager": ProfileExperience(title=t, company="Acme", description="", start=s, end=e)
    stable = _profile(experiences=[exp("Mar 2023", "Present"), exp("Jan 2019", "Feb 2023")])
    hoppy = _profile(experiences=[exp("Mar 2025", "Present"), exp("Jan 2024", "Jun 2024"), exp("Jan 2023", "Aug 2023"),
                                  exp("Jun 2022", "Sep 2022", "Marketing Intern")])
    a = _cat(scoring.compute_profile(stable, "Marketing Manager", [], today), "Career timeline")
    b = _cat(scoring.compute_profile(hoppy, "Marketing Manager", [], today), "Career timeline")
    assert a.score == 100
    assert b.score < 40 and "2 of 2 recent finished roles under a year" in b.detail  # the internship isn't held against them
    year_only = _cat(scoring.compute_profile(_profile(experiences=[exp("2019", "2023")]), "Marketing Manager", [], today), "Career timeline")
    assert year_only.score == 75  # full stint/gap credit, no month precision


def test_network_tiers_and_unknowns_are_excluded():
    assert scoring._network(600)[0] == 100 and scoring._network(320)[0] == 80 and scoring._network(10)[0] == 20
    card = scoring.compute_profile(_profile(connections=None), "Marketing Manager", [])
    assert _cat(card, "Network").weight == 0  # PDF export has no connection count


def test_open_to_work_is_reported_not_scored_and_contact_counts_when_known():
    on = scoring.compute_profile(_profile(open_to_work=True, has_contact=True), "Marketing Manager", [])
    off = scoring.compute_profile(_profile(open_to_work=False, has_contact=True), "Marketing Manager", [])
    assert _cat(on, "Profile completeness").score == _cat(off, "Profile completeness").score == 100
    assert "Open to Work frame on" in _cat(on, "Profile completeness").detail
    no_contact = scoring.compute_profile(_profile(has_contact=False, name="JANE DOE"), "Marketing Manager", [])
    assert "email or website" in _cat(no_contact, "Profile completeness").detail and "capitalization" in _cat(no_contact, "Profile completeness").detail


def test_abbreviation_counts_for_the_skill_but_not_for_literal_ats_coverage():
    resume = demo.DEMO_RESUME + "\n- Deployed services on K8s and GCP"
    job = JobRequirements(job_title="Engineer", company="Acme", requirements=[
        Requirement(id="r1", kind="skill", text="Kubernetes", importance="must", keywords=["Kubernetes"], min_years=None),
    ])
    ev = ResumeEvidence(candidate_name="X", evidence=[Evidence(id="r1", status="met", quote="", years=None)])
    card = scoring.compute(resume, job, ev)
    r = card.requirements[0]
    assert r.status == "met" and r.matched_keywords == []
    assert '"k8s"' in r.note and '"Kubernetes" word-for-word' in r.note
    assert "Kubernetes" in card.missing_keywords  # still worth adding literally
    ats = next(c for c in card.categories if c.name == "ATS keyword coverage")
    assert ats.score == 0


def test_ambiguous_short_forms_are_not_synonyms():
    from app.synonyms import variants
    assert "k8s" in variants("Kubernetes") and "Kubernetes".lower() in [v.lower() for v in variants("k8s")]
    assert variants("Golang") == ["go lang"]  # bare "go" would match ordinary English


def test_experience_years_are_computed_from_dates_not_guessed():
    today = scoring.date(2026, 10, 1)
    resume = "Product Designer — Brightly (2021–present)\nUX Designer — Northwind (2019–2021)\nBA — OCAD University (2015–2019)"
    assert scoring.experience_years(resume, today) == 7.2  # mid-2019 to Oct 2026, the degree isn't a role
    assert "Today is October 2026" in scoring.durations_block(resume, today)
    job = JobRequirements(job_title="Designer", company="X", requirements=[
        Requirement(id="r1", kind="experience", text="5+ years of design", importance="must", keywords=[], min_years=5),
        Requirement(id="r2", kind="experience", text="10+ years of design", importance="nice", keywords=[], min_years=10),
    ])
    # the model under-counts (no idea what today is) for r1 and over-counts for r2: code fixes both
    ev = ResumeEvidence(candidate_name="X", evidence=[Evidence(id="r1", status="partial", quote="", years=4.5),
                                                      Evidence(id="r2", status="met", quote="", years=12)])
    r1, r2 = sorted(scoring.compute(resume, job, ev, today).requirements, key=lambda r: r.id)
    assert r2.status == "missing" and "7.2 years" in r2.note  # capped at what the dates add up to
    assert r1.status == "partial" and "4.5 years" in r1.note  # the model's pick of relevant roles is kept when lower


def test_month_dates_and_overlaps():
    today = scoring.date(2026, 10, 1)
    resume = "Lead, Acme — Jan 2020 - Present\nFreelance — 03/2021 to 06/2022\nAnalyst, Beta — Mar 2016 – Dec 2018"
    assert scoring.experience_years(resume, today) == 9.5  # 2y9m + 6y9m, the freelance overlap counted once


def test_keyword_dump_is_detected_but_skills_section_is_not():
    resume = demo.DEMO_RESUME + "\nKeywords: Figma, WCAG, design system, Storybook, design tokens\n"
    assert scoring.keyword_dump_lines(resume) == ["Keywords: Figma, WCAG, design system, Storybook, design tokens"]
    assert scoring.keyword_dump_lines(demo.DEMO_RESUME) == []  # the real Skills section stays
    long_list = "Summary\nDesigner.\n" + ", ".join(f"term{i}" for i in range(12)) + "\nSkills\n" + ", ".join(f"s{i}" for i in range(12))
    assert scoring.keyword_dump_lines(long_list) == [", ".join(f"term{i}" for i in range(12))]


def test_keywords_only_in_the_skills_list_get_half_credit():
    job = JobRequirements(job_title="Designer", company="Acme", requirements=[
        Requirement(id="r1", kind="skill", text="Sketch", importance="must", keywords=["Sketch"], min_years=None),
        Requirement(id="r2", kind="skill", text="Usability testing", importance="must", keywords=["usability testing"], min_years=None),
    ])
    ev = ResumeEvidence(candidate_name="X", evidence=[Evidence(id="r1", status="partial", quote="", years=None),
                                                      Evidence(id="r2", status="met", quote="", years=None)])
    ats = next(c for c in scoring.compute(demo.DEMO_RESUME, job, ev).categories if c.name == "ATS keyword coverage")
    assert ats.score == 75  # usability testing in a bullet (full), Sketch only in Skills (half)
    assert "1 only in your skills list" in ats.detail


def test_title_alignment_checks_recent_title_and_seniority():
    today = scoring.date(2026, 10, 1)
    t = scoring.title_alignment("Senior Product Designer", demo.DEMO_RESUME, today)
    assert t["score"] == 50 and "not the Senior level" in t["note"]  # title matches, level doesn't
    assert scoring.title_alignment("Product Designer", demo.DEMO_RESUME, today)["score"] == 100
    assert scoring.title_alignment("Data Engineer", demo.DEMO_RESUME, today)["score"] == 0


def test_skills_shown_only_in_an_old_role_count_as_partial():
    today = scoring.date(2026, 10, 1)
    resume = ("Experience\nProduct Designer — Brightly (2021–present)\n- Ran usability testing with 12 customers\n"
              "Web Developer — Oldco (2010–2016)\n- Built Flash microsites for 30 clients\n")
    job = JobRequirements(job_title="Designer", company="X", requirements=[
        Requirement(id="r1", kind="skill", text="Flash", importance="nice", keywords=["Flash"], min_years=None),
        Requirement(id="r2", kind="skill", text="Usability testing", importance="must", keywords=["usability testing"], min_years=None),
    ], gates=["Authorised to work in Canada"])
    ev = ResumeEvidence(candidate_name="X", evidence=[
        Evidence(id="r1", status="met", quote="Built Flash microsites for 30 clients", years=None),
        Evidence(id="r2", status="met", quote="Ran usability testing with 12 customers", years=None)])
    card = scoring.compute(resume, job, ev, today)
    by = {r.id: r for r in card.requirements}
    assert by["r1"].status == "partial" and "ended in 2016" in by["r1"].note
    assert by["r2"].status == "met"
    assert card.gates == ["Authorised to work in Canada"]


def test_edit_gains_lenses_and_skim():
    job, ev = demo_extraction()
    card = scoring.compute(demo.DEMO_RESUME, job, ev)
    assert card.lenses.strict_total == sum(bool(r.keywords) for r in job.requirements)
    assert not card.skim.title_shown and "does not name" in card.skim.verdict  # "Senior" isn't at the top
    from app.models import Change
    add_wcag = Change(priority="high", section="Experience", issue="", why="",
                      current="Worked on UI components for the web app.",
                      suggested="Built and documented the Figma design system and component library to WCAG 2.2 AA.")
    missing_line = Change(priority="low", section="Summary", issue="", why="", current="Not in the resume at all", suggested="x")
    changes, projected = scoring.edit_gains(demo.DEMO_RESUME, [add_wcag, missing_line],
                                            lambda t: scoring.compute(t, job, ev).overall, card.overall)
    assert changes[0].gain > 0 and changes[1].gain is None
    assert projected >= card.overall + changes[0].gain


def test_majority_vote_keeps_the_median_verdict_and_a_real_quote():
    real = "Led usability testing for the onboarding redesign with 12 customers each quarter."
    vote = lambda *items: ResumeEvidence(candidate_name="Demo", evidence=[Evidence(id=i, status=s, quote=q, years=y) for i, s, q, y in items])
    merged = scoring.merge_evidence([
        vote(("r1", "met", "Invented line not in the resume", None), ("r2", "met", real, None), ("r3", "met", "", 4.5)),
        vote(("r1", "met", real, None), ("r2", "partial", "", None), ("r3", "partial", "", 7.2)),
        vote(("r1", "partial", "", None), ("r2", "missing", "", None), ("r3", "met", "", 5)),
    ], demo.DEMO_RESUME)
    by = {e.id: e for e in merged.evidence}
    assert by["r1"].status == "met" and by["r1"].quote == real  # 2 of 3 say met; the quote that's really there wins
    assert by["r2"].status == "partial"  # met / partial / missing -> the middle one
    assert by["r3"].status == "met" and by["r3"].years == 5  # median years
    assert scoring.merge_evidence([merged], demo.DEMO_RESUME) is merged  # one reading: unchanged


def test_assessment_confidence_and_decision():
    job, ev = demo_extraction()
    card = scoring.compute(demo.DEMO_RESUME, job, ev)
    full_jd = "Senior Product Designer at Acme Cloud. " * 40
    a = scoring.assess(card, full_jd, demo.DEMO_RESUME, projected=78)
    assert a.decision == "apply_after_edits" and "to 78" in a.decision_reason  # 55 now, 78 after edits
    assert a.confidence in ("high", "medium") and len(a.confidence_reasons) == 3
    thin = scoring.assess(card, "Designer role", demo.DEMO_RESUME, projected=None)
    assert thin.decision == "provisional" and thin.confidence == "low"
    low = scoring.assess(card, full_jd, demo.DEMO_RESUME, projected=None)
    assert low.decision == "stretch" and "Missing must-haves" in low.decision_reason


def test_writing_flags():
    text = ("Passionate, results-driven designer. Canadian permanent resident.\n- Grew signups 40%\n- Cut churn, lifting signups 40%\n"
            "- Signups up 40% again\nAuthorized to work in Canada")
    flags = scoring.writing_flags(text)
    assert any("passionate" in f for f in flags) and any("40%" in f for f in flags) and any("2 times" in f for f in flags)
    assert scoring.writing_flags(demo.DEMO_RESUME) == [f for f in scoring.writing_flags(demo.DEMO_RESUME) if "passionate" in f.lower()]
