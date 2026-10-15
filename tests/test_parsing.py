from app.models import Job
from app.parsing import _html_to_text, linkedin_job_id, linkedin_profile_id, merge_jobs, reflow


def test_reflow_rejoins_pdf_bullets_and_wrapped_lines():
    raw = "KEY ACHIEVEMENTS\n •\nGenerated 2,000+ user signups and 106 paid course enrollments through integrated campaigns\nat ilmX.\n•\nDeveloped brand messaging for 15+ brands."
    lines = reflow(raw).splitlines()
    assert lines[1] == "• Generated 2,000+ user signups and 106 paid course enrollments through integrated campaigns at ilmX."
    assert lines[2] == "• Developed brand messaging for 15+ brands."


def test_reflow_keeps_structured_lines_apart():
    raw = ("Lahore • +92 300 0000000 • name@example.com • LinkedIn • Portfolio\n"
           "Marketing professional with 4+ years of experience across EdTech and agencies, combining strategy.\n"
           "MS Business Analytics | FAST-NUCES | 2022 - 2024\n"
           "Certifications: HubSpot (2025)")
    assert len(reflow(raw).splitlines()) == 4


def test_html_to_text_keeps_loose_text():
    html = "<strong>About Acme<br><br></strong>Acme builds things.<ul><li>Own the roadmap</li></ul>"
    text = _html_to_text(html)
    assert "About Acme" in text and "Acme builds things." in text and "• Own the roadmap" in text


def test_merge_prefers_fullest_description_and_fills_gaps():
    a = Job(title="Designer", company="", description="short", criteria=["Seniority level: Senior"], sources=["Bright Data"])
    b = Job(title="", company="Acme", description="a much longer description", criteria=["Seniority level: Mid", "Salary: $1"], sources=["Apify"])
    m = merge_jobs([a, b])
    assert (m.title, m.company, m.description) == ("Designer", "Acme", "a much longer description")
    assert m.criteria == ["Seniority level: Senior", "Salary: $1"]
    assert m.sources == ["Bright Data", "Apify"]


def test_linkedin_ids():
    assert linkedin_job_id("https://www.linkedin.com/jobs/view/senior-designer-4459772101/") == "4459772101"
    assert linkedin_job_id("https://www.linkedin.com/jobs/search/?currentJobId=4459772101") == "4459772101"
    assert linkedin_profile_id("https://www.linkedin.com/in/Jane-Doe/?trk=x") == "jane-doe"
