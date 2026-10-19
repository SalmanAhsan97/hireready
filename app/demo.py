"""Canned responses used when no Anthropic credentials are configured, so the UI can be tried end to end."""

# Demo mode scores this resume (not the uploaded file) with the real scorer, so the numbers on screen
# come from scoring.py exactly as they would with live extraction.
DEMO_RESUME = """Demo Candidate
Toronto, ON · demo@example.com · +1 416 555 0134 · portfolio.example.com

Summary
Passionate designer who loves solving problems.

Experience
Product Designer — Brightly (2021–present)
- Worked on UI components for the web app.
- Led usability testing for the onboarding redesign with 12 customers each quarter.
- Ran user research interviews and turned findings into journey mapping workshops for the product team.
- Built high-fidelity interactive prototypes in Figma for every major release of the SaaS platform.
- Collaborated with engineers and product managers on sprint planning and design reviews.
- Collaborated with other designers on shared patterns and critique sessions.

UX Designer — Northwind Studio (2019–2021)
- Designed responsive marketing sites and dashboards for small business clients.
- Created wireframes, user flows and clickable prototypes for client sign-off.
- Presented design concepts to stakeholders and iterated on their feedback each week.

Skills
Figma, Sketch, Miro, prototyping, user research, usability testing, journey mapping

Education
BA, Interaction Design — OCAD University (2019)
"""

_REQUIREMENTS = [
    ("r1", "skill", "Expert in Figma", "must", ["Figma"], None),
    ("r2", "experience", "5+ years of product design experience", "must", [], 5),
    ("r3", "skill", "Built or maintained a design system", "must", ["design system", "component library"], None),
    ("r4", "skill", "Accessibility expertise (WCAG 2.2 AA)", "must", ["WCAG", "accessibility"], None),
    ("r5", "skill", "User research and usability testing", "must", ["usability testing", "user research"], None),
    ("r6", "title", "Senior-level product designer", "must", ["Senior Product Designer"], None),
    ("r7", "experience", "B2B SaaS product experience", "must", ["B2B", "SaaS"], None),
    ("r8", "skill", "Cross-functional leadership with engineering and PM", "must", ["cross-functional"], None),
    ("r9", "skill", "Interactive prototyping", "nice", ["prototyping", "prototype"], None),
    ("r10", "skill", "Mentoring other designers", "nice", ["mentoring", "mentored"], None),
    ("r11", "skill", "Design tokens", "nice", ["design tokens"], None),
    ("r12", "skill", "A/B testing", "nice", ["A/B testing", "experimentation"], None),
    ("r13", "skill", "Storybook or similar dev handoff", "nice", ["Storybook"], None),
    ("r14", "skill", "Journey mapping", "nice", ["journey mapping"], None),
    ("r15", "education", "Degree in interaction design, HCI or related", "nice", ["Interaction Design", "HCI"], None),
]

_EVIDENCE = [
    ("r1", "met", "Built high-fidelity interactive prototypes in Figma for every major release of the SaaS platform.", None),
    ("r2", "met", "Product Designer — Brightly (2021–present)", 5),
    ("r3", "partial", "Worked on UI components for the web app.", None),
    ("r4", "missing", "", None),
    ("r5", "met", "Led usability testing for the onboarding redesign with 12 customers each quarter.", None),
    ("r6", "partial", "Product Designer — Brightly (2021–present)", None),
    ("r7", "partial", "Built high-fidelity interactive prototypes in Figma for every major release of the SaaS platform.", None),
    ("r8", "partial", "Collaborated with engineers and product managers on sprint planning and design reviews.", None),
    ("r9", "met", "Created wireframes, user flows and clickable prototypes for client sign-off.", None),
    ("r10", "missing", "", None),
    ("r11", "missing", "", None),
    ("r12", "missing", "", None),
    ("r13", "missing", "", None),
    ("r14", "met", "Ran user research interviews and turned findings into journey mapping workshops for the product team.", None),
    ("r15", "met", "BA, Interaction Design — OCAD University (2019)", None),
]

DEMO_EXTRACTION = {
    "job": {
        "job_title": "Senior Product Designer",
        "company": "Acme Cloud",
        "requirements": [
            {"id": i, "kind": k, "text": t, "importance": imp, "keywords": kw, "min_years": y}
            for i, k, t, imp, kw, y in _REQUIREMENTS
        ],
    },
    "resume": {
        "candidate_name": "Demo Candidate",
        "evidence": [{"id": i, "status": st, "quote": q, "years": y} for i, st, q, y in _EVIDENCE],
    },
}

DEMO_NARRATIVE = {
    "verdict": "Solid research and prototyping craft, but the resume misses the accessibility must-have and undersells design-system ownership — the two things this posting stresses most.",
    "rationales": [
        {"name": "Hard skills & tools", "rationale": "Figma, research and prototyping are clearly shown; design-system work is only implied, accessibility is absent, and none of the nice-to-have tools (tokens, Storybook, A/B testing) appear."},
        {"name": "Relevant experience & seniority", "rationale": "Five years of design meets the bar, but titles never say Senior and B2B SaaS context is thin."},
        {"name": "ATS keyword coverage", "rationale": "An ATS would find Figma, usability testing and SaaS, but not 'design system', 'WCAG' or 'cross-functional', which the posting repeats."},
        {"name": "Impact & quantified results", "rationale": "Bullets describe duties, not outcomes — only the usability-testing line carries a number."},
        {"name": "Education & certifications", "rationale": "A BA in Interaction Design matches the preferred background."},
        {"name": "Clarity & formatting", "rationale": "Clean single-column structure with all standard sections; at under 250 words it leaves out detail recruiters look for."},
    ],
    "strengths": [
        "Five years of end-to-end product design on SaaS products.",
        "Hands-on research practice (interviews, usability tests).",
        "Portfolio link up front.",
    ],
    "gaps": [
        "No evidence of owning or contributing to a design system — a stated must-have.",
        "Accessibility isn't mentioned anywhere.",
        "Results aren't quantified.",
    ],
    "red_flags": ["Accessibility (WCAG 2.2 AA) is a must-have and the resume never mentions it."],
    "changes": [
        {"priority": "high", "section": "Experience — Brightly", "issue": "Design-system work is buried as a duty.", "current": "Worked on UI components for the web app.", "suggested": "Built and documented 40+ components in Brightly's Figma design system, cutting new-feature design time by [X%] across 4 squads.", "why": "Turns a vague duty into the posting's #1 requirement with a measurable result."},
        {"priority": "high", "section": "Skills", "issue": "Accessibility, a must-have, is missing entirely.", "current": "Figma, Sketch, Miro, prototyping, user research, usability testing, journey mapping", "suggested": "Figma (variables, design tokens), Storybook handoff, WCAG 2.2 AA audits, usability testing, A/B testing, user research, journey mapping", "why": "Adds the accessibility must-have and 4 nice-to-have keywords — only list what you can truthfully claim."},
        {"priority": "high", "section": "Summary", "issue": "Generic summary with no role fit.", "current": "Passionate designer who loves solving problems.", "suggested": "Senior product designer with 5 years in B2B SaaS, specialising in scalable design systems and accessible (WCAG 2.2 AA) interfaces.", "why": "Front-loads the posting's top keywords for ATS and the 6-second recruiter skim."},
        {"priority": "medium", "section": "Experience — Brightly", "issue": "Cross-functional work reads as attendance, not leadership.", "current": "Collaborated with engineers and product managers on sprint planning and design reviews.", "suggested": "Led cross-functional design reviews with engineering and product for [N] squads, cutting rework before release by [X%].", "why": "Hits the 'cross-functional leadership' must-have word-for-word, with an outcome."},
        {"priority": "medium", "section": "Experience — Brightly", "issue": "No mentoring shown.", "current": "Collaborated with other designers on shared patterns and critique sessions.", "suggested": "Mentored 2 junior designers through weekly critiques and onboarding to the design system.", "why": "Covers the mentoring nice-to-have — only if true."},
        {"priority": "low", "section": "Experience — Northwind Studio", "issue": "Results aren't quantified.", "current": "Designed responsive marketing sites and dashboards for small business clients.", "suggested": "Designed responsive sites and dashboards for [N] small-business clients, lifting conversion by [X%] on average.", "why": "Only 1 of 9 bullets carries a number; recruiters skim for outcomes."},
    ],
}

# Resume-only mode (no job link): facts the model would extract from DEMO_RESUME.
DEMO_PROFILE = {
    "candidate_name": "Demo Candidate",
    "target_role": "Product Designer",
    "seniority": "mid",
    "hard_skills": ["Figma", "Sketch", "Miro", "prototyping", "user research", "usability testing", "journey mapping", "wireframes", "user flows"],
    "expected_keywords": [{"term": k, "aliases": []} for k in [
        "Figma", "prototyping", "user research", "usability testing", "design systems", "wireframes",
        "interaction design", "accessibility", "A/B testing", "journey mapping", "information architecture",
        "user flows", "stakeholder management", "design thinking", "cross-functional",
    ]],
    "roles": [
        {"title": "Product Designer", "company": "Brightly", "start": "2021", "end": "present", "bullets": 6},
        {"title": "UX Designer", "company": "Northwind Studio", "start": "2019", "end": "2021", "bullets": 3},
    ],
}

DEMO_RESUME_NARRATIVE = {
    "verdict": "A solid, well-structured product design resume that undersells its impact and misses several keywords recruiters search for. Add a job link to see how it matches a specific role.",
    "rationales": [
        {"name": "ATS keyword coverage", "rationale": "Core terms like Figma, prototyping and user research are there, but design systems, accessibility and A/B testing — standard for this role — never appear."},
        {"name": "Impact & quantified results", "rationale": "Only the usability-testing bullet has a number; the rest describe duties rather than outcomes."},
        {"name": "Action-oriented bullets", "rationale": "Most bullets open with strong verbs like Led, Built and Designed; 'Worked on UI components' is the one weak opener."},
        {"name": "Clarity & formatting", "rationale": "Clean single-column layout with standard sections, but at under 250 words it leaves out detail recruiters look for."},
        {"name": "Skills section", "rationale": "Nine concrete tools and methods are named; adding a few more you genuinely use would round it out."},
        {"name": "Experience detail", "rationale": "Both roles have dates and at least three bullets, which is exactly what recruiters expect."},
        {"name": "Contact & profile", "rationale": "Email, phone and a summary are present, but there's no LinkedIn profile link."},
    ],
    "strengths": [
        "Clear, dated work history with two relevant design roles.",
        "Hands-on research practice: interviews, usability testing and journey mapping.",
        "Concrete tool list that ATS filters can match.",
    ],
    "gaps": [
        "Almost no measurable results — only one bullet has a number.",
        "No mention of design systems or accessibility, both standard for product designers.",
        "No LinkedIn profile link and a generic summary.",
    ],
    "red_flags": [],
    "changes": [
        {"priority": "high", "section": "Experience — Brightly", "issue": "Design-system work is described as a vague duty.", "current": "Worked on UI components for the web app.", "suggested": "Built and documented [N] reusable components in Brightly's Figma design system, cutting new-feature design time by [X%].", "why": "Adds the 'design systems' keyword with a measurable outcome."},
        {"priority": "high", "section": "Summary", "issue": "Generic summary.", "current": "Passionate designer who loves solving problems.", "suggested": "Product designer with 5 years in SaaS, specialising in research-led interaction design, prototyping and accessible design systems.", "why": "Front-loads the keywords recruiters search for."},
        {"priority": "high", "section": "Experience — Northwind Studio", "issue": "Results aren't quantified.", "current": "Designed responsive marketing sites and dashboards for small business clients.", "suggested": "Designed responsive sites and dashboards for [N] small-business clients, lifting conversion by [X%] on average.", "why": "Recruiters skim for outcomes; numbers make them stick."},
        {"priority": "medium", "section": "Experience — Brightly", "issue": "Weak opener.", "current": "Collaborated with engineers and product managers on sprint planning and design reviews.", "suggested": "Led cross-functional design reviews with engineering and product, catching [N] usability issues before release.", "why": "Stronger verb plus the 'cross-functional' keyword."},
        {"priority": "medium", "section": "Header", "issue": "No LinkedIn profile link.", "current": "Toronto, ON · demo@example.com · +1 416 555 0134 · portfolio.example.com", "suggested": "Toronto, ON · demo@example.com · +1 416 555 0134 · linkedin.com/in/[your-handle] · portfolio.example.com", "why": "Recruiters check LinkedIn first; ATS profiles often require it."},
        {"priority": "low", "section": "Skills", "issue": "Missing common expected skills.", "current": "Figma, Sketch, Miro, prototyping, user research, usability testing, journey mapping", "suggested": "Figma, Sketch, Miro, prototyping, user research, usability testing, journey mapping, information architecture, accessibility (WCAG), A/B testing", "why": "Adds expected keywords — only list what you can back up."},
    ],
}

DEMO_KIT = {
    "positioning_statement": "A product designer who makes teams faster by building the systems other designers ship on. Pitch yourself as the person who turns Acme's growing component sprawl into an accessible, token-driven design system.",
    "projected_score": 82,
    "tailored_resume_markdown": "# Demo Candidate\nToronto, ON · demo@example.com · portfolio.example.com\n\n## Summary\nSenior product designer with 5 years in B2B SaaS, specialising in scalable design systems and accessible (WCAG 2.2 AA) interfaces.\n\n## Experience\n### Product Designer — Brightly (2021–present)\n- Built and documented 40+ components in Brightly's Figma design system, cutting new-feature design time by [X%] across 4 squads.\n- Led usability testing for onboarding redesign; activation rose [X%].\n- Mentored 2 junior designers through weekly critiques.\n\n## Skills\nFigma (variables, design tokens) · Storybook handoff · WCAG 2.2 audits · Usability & A/B testing\n\n## Education\nBA, Interaction Design",
    "cover_letter": "Dear Acme Cloud hiring team,\n\nAcme's posting talks about a design system that has to keep up with five product teams. That's the problem I've spent the last three years on at Brightly...\n\n(Demo text — connect an API key for a real letter.)",
    "linkedin_connection_note": "Hi Sam — I'm applying for the Senior Product Designer role. I've spent 3 years building a design system used by 4 squads and would love to learn how Acme's team is approaching theirs.",
    "recruiter_message": "Thanks for connecting! I just applied for the Senior Product Designer role. Quick fit summary: 5 yrs B2B SaaS, built a 40-component design system, WCAG 2.2 audits. Happy to share a case study if useful.",
    "hiring_manager_email_subject": "Senior Product Designer — design systems at scale",
    "hiring_manager_email_body": "Hi Sam,\n\nI applied for the Senior Product Designer role today...\n\n(Demo text.)",
    "who_to_contact": [
        "Head of Design / Design Manager — search \"Acme Cloud design manager\" on LinkedIn People",
        "Technical recruiter for design roles — check who posted the job",
        "A senior designer on the design-system team — ask about the team, not the job",
    ],
    "interview_talking_points": [
        {"question": "Tell us about a design system you built.", "answer_outline": "Brightly: problem (inconsistent UI across 4 squads) → approach (audit, tokens, Figma library + Storybook) → result ([X%] faster)."},
        {"question": "How do you approach accessibility?", "answer_outline": "WCAG 2.2 AA baked into components; contrast tokens; screen-reader testing in QA."},
    ],
    "application_checklist": [
        "Fill the [placeholders] in the tailored resume with real numbers.",
        "Export the resume as DOCX/PDF and apply on the LinkedIn job page.",
        "Send the connection note to the recruiter the same day.",
        "Follow up with the recruiter message 3–4 business days later.",
        "Prepare the two case studies referenced in the talking points.",
    ],
}

DEMO_CHAT_REPLY = "Resume editing needs an AI model, and Screenproof isn't connected to one right now — so this is a sample reply and your resume wasn't changed."

# LinkedIn review demo: a profile, eight short postings and the model's reading of them. The search map,
# evidence map, recruiter searches and score are all computed from these by the real code.
DEMO_LI_PROFILE = {
    "name": "Demo Candidate",
    "headline": "Product Designer at Brightly | Figma, prototyping and usability testing for SaaS",
    "about": "I design web products for SaaS teams. At Brightly I run usability testing with 12 customers each quarter "
             "and turn what we learn into prototypes the team can ship. Before that I designed dashboards and marketing "
             "sites for small business clients at Northwind Studio.",
    "location": "Toronto, Ontario, Canada",
    "experiences": [
        {"title": "Product Designer", "company": "Brightly", "start": "Mar 2021", "end": "Present",
         "description": "Led usability testing for the onboarding redesign with 12 customers each quarter. Built "
                        "high-fidelity prototypes in Figma for every major release. Ran user research interviews and "
                        "journey mapping workshops with product and engineering."},
        {"title": "UX Designer", "company": "Northwind Studio", "start": "Jun 2019", "end": "Feb 2021",
         "description": "Designed responsive marketing sites and dashboards. Created wireframes and user flows for client sign-off."},
    ],
    "skills": ["Figma", "Prototyping", "User Research", "Usability Testing", "Design Systems"],
    "skills_total": 14, "certifications": 0, "recommendations": 1, "education": 1, "connections": 420,
    "has_photo": True, "has_banner": False, "has_featured": False, "custom_url": True,
    "source": "Demo profile", "open_to_work": None, "has_contact": True,
}

DEMO_POSTINGS = [
    ("Senior Product Designer", "SaaS company. Figma, design systems, user research, prototyping. Improve activation and retention."),
    ("Product Designer", "B2B SaaS. Figma, prototyping, usability testing, accessibility (WCAG). Drive conversion."),
    ("Product Designer II", "Fintech. Figma, design systems, user research, A/B testing. Improve conversion."),
    ("UX Designer", "Agency. Wireframes, user flows, Figma, usability testing."),
    ("Senior Product Designer", "SaaS. Design systems, Figma, accessibility, cross-functional work with engineering. Retention."),
    ("Product Designer", "E-commerce. Figma, prototyping, A/B testing, user research. Conversion rate."),
    ("Lead Product Designer", "SaaS. Design systems, mentoring, Figma, user research. Activation."),
    ("UX/UI Designer", "Healthcare. Figma, accessibility, wireframes, usability testing."),
]

DEMO_POSTING_FACTS = [
    {"index": 1, "title": "Product Designer", "industry": "SaaS", "skills": ["design systems", "user research", "prototyping"], "tools": ["Figma"], "outcomes": ["activation", "retention"]},
    {"index": 2, "title": "Product Designer", "industry": "SaaS", "skills": ["prototyping", "usability testing", "accessibility"], "tools": ["Figma"], "outcomes": ["conversion"]},
    {"index": 3, "title": "Product Designer", "industry": "Fintech", "skills": ["design systems", "user research", "A/B testing"], "tools": ["Figma"], "outcomes": ["conversion"]},
    {"index": 4, "title": "UX Designer", "industry": "Agency", "skills": ["wireframes", "user flows", "usability testing"], "tools": ["Figma"], "outcomes": []},
    {"index": 5, "title": "Product Designer", "industry": "SaaS", "skills": ["design systems", "accessibility", "cross-functional collaboration"], "tools": ["Figma"], "outcomes": ["retention"]},
    {"index": 6, "title": "Product Designer", "industry": "E-commerce", "skills": ["prototyping", "A/B testing", "user research"], "tools": ["Figma"], "outcomes": ["conversion"]},
    {"index": 7, "title": "Product Designer", "industry": "SaaS", "skills": ["design systems", "mentoring", "user research"], "tools": ["Figma"], "outcomes": ["activation"]},
    {"index": 8, "title": "UX Designer", "industry": "Healthcare", "skills": ["accessibility", "wireframes", "usability testing"], "tools": ["Figma"], "outcomes": []},
]

DEMO_LI_NARRATIVE = {
    "verdict": "Recruiters searching for a Product Designer will find you, but searches that add design systems or accessibility pass you by, because your experience never shows them.",
    "rationales": [
        {"name": "Headline", "rationale": "Names the title and three of the skills postings ask for most."},
        {"name": "About section", "rationale": "Clear and specific, but short: 50 words where 200 lets you work in more of the search terms."},
        {"name": "Keyword coverage", "rationale": "Figma, prototyping and user research are covered; design systems, accessibility and A/B testing are not shown in your experience."},
        {"name": "Experience descriptions", "rationale": "Brightly is well described with a number; Northwind is short and has no result."},
        {"name": "Experience titles", "rationale": "Both titles match the design titles postings use."},
        {"name": "Career timeline", "rationale": "No gaps, and both roles ran over a year."},
        {"name": "Skills", "rationale": "14 skills listed; adding the essential terms you can back up helps search ranking."},
        {"name": "Network", "rationale": "420 connections; 500+ is where recruiters stop noticing."},
        {"name": "Credibility", "rationale": "One recommendation and a degree; no certifications."},
        {"name": "Profile completeness", "rationale": "Add a banner and a Featured section."},
    ],
    "strengths": ["Headline names the target title", "Usability testing is backed by a concrete number", "Clean, gap-free timeline"],
    "gaps": ["Design systems is listed in Skills but no role shows it", "Accessibility and A/B testing are nowhere on the profile", "Northwind Studio has no result"],
    "red_flags": [],
    "changes": [
        {"priority": "high", "section": "Experience — Brightly", "issue": "Design systems is only a listed skill.", "current": "Built high-fidelity prototypes in Figma for every major release.", "suggested": "Built high-fidelity prototypes in Figma for every major release, using [the shared component library you maintained].", "why": "Half the postings ask for design systems; recruiters trust it more when a role shows it."},
        {"priority": "medium", "section": "About", "issue": "Short for search.", "current": "I design web products for SaaS teams.", "suggested": "I design web products for SaaS teams, from user research to tested Figma prototypes.", "why": "About is searched; more of the terms recruiters use, in your own words, helps."},
    ],
    "top_half": [
        {"headline": "Product Designer | Usability testing, user research and Figma prototyping for SaaS", "about_opening": "I design SaaS products from research to tested prototype. At Brightly I run usability tests with 12 customers each quarter and turn the findings into Figma prototypes the team ships."},
        {"headline": "Product Designer at Brightly | Research-led SaaS design in Figma", "about_opening": "Most of my work starts with customers: interviews, usability tests and journey maps, then prototypes in Figma for every major release."},
        {"headline": "Product Designer | Turning usability research into shipped SaaS features", "about_opening": "I help SaaS teams ship features customers can use the first time, by testing with real customers every quarter and prototyping in Figma before code."},
    ],
    "experience_rewrites": [
        {"company": "Brightly", "title": "Product Designer", "problem": "New customers were getting stuck during onboarding.", "action": "Led usability testing with 12 customers each quarter and redesigned the onboarding flow in Figma.", "result": "[Onboarding completion rose X%]", "skill": "usability testing", "text": "New customers were getting stuck in onboarding. I led usability testing with 12 customers each quarter and redesigned the flow in Figma; [onboarding completion rose X%]."},
        {"company": "Northwind Studio", "title": "UX Designer", "problem": "Clients needed to sign off designs before build.", "action": "Created wireframes and user flows for each client.", "result": "[N projects signed off on first review]", "skill": "wireframes", "text": "Clients needed to approve designs before build, so I created wireframes and user flows for each one; [N projects signed off on first review]."},
    ],
    "proof_questions": [
        {"term": "design systems", "question": "Have you built or maintained a shared component library at Brightly or Northwind? Which one, and roughly how many components?"},
        {"term": "accessibility", "question": "Have you designed to WCAG or run accessibility checks on any project? Which one?"},
        {"term": "A/B testing", "question": "Have any of your designs been A/B tested? What changed in the result?"},
    ],
}
