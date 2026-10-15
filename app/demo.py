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
