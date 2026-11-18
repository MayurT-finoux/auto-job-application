from jobbot.email_finder import choose_email, extract_emails
from jobbot.matcher import experience_ok, parse_experience, score_lead
from jobbot.search.base import Lead, company_key, root_domain
from jobbot.search.duckduckgo import guess_company


def test_parse_experience():
    assert parse_experience("Looking for 3-5 years of experience") == (3, 5)
    assert parse_experience("2 to 4 yrs exp") == (2, 4)
    assert parse_experience("5+ years in Python") == (5, 99)
    assert parse_experience("minimum 4 years") == (4, 99)
    assert parse_experience("6 years of experience required") == (6, 99)
    assert parse_experience("Great culture, apply now") is None


def test_experience_overlap():
    assert experience_ok("3-5 years", 2, 5)
    assert experience_ok("1-3 years", 2, 5)
    assert not experience_ok("7-10 years", 2, 5)
    assert not experience_ok("8+ years", 2, 5)
    assert experience_ok("no range mentioned", 2, 5)


def test_extract_and_filter_emails():
    text = ("Send CV to HR@Acme.co.in or careers [at] acme [dot] co [dot] in. "
            "logo@2x.png noreply@acme.co.in jobs@naukri.com")
    emails = extract_emails(text)
    assert "hr@acme.co.in" in emails
    assert "careers@acme.co.in" in emails
    assert not any("png" in e or "noreply" in e or "naukri" in e for e in emails)


def test_choose_email_prefers_hr_and_company_domain():
    emails = ["info@acme.com", "hr@acme.com", "someone@other.com"]
    assert choose_email(emails, company_domain="https://www.acme.com") == "hr@acme.com"
    assert choose_email(["x@gmail.com"], company_domain="acme.com") is None
    assert choose_email(["x@gmail.com"], trusted=True) == "x@gmail.com"


def test_root_domain_and_company_key():
    assert root_domain("https://careers.acme.co.in/jobs") == "acme.co.in"
    assert root_domain("www.acme.com") == "acme.com"
    assert company_key("Acme Technologies Pvt. Ltd.") == company_key("ACME")


def test_score_lead():
    lead = Lead(company="Acme", title="Python Developer - Acme", url="",
                snippet="Django, REST APIs and AWS. 2-4 years.")
    role, score = score_lead(lead, ["Python Developer", "Backend Developer"], ["python", "django", "aws"])
    assert role == "Python Developer" and score == 1.0
    off = Lead(company="X", title="Sales Executive", url="", snippet="field sales, targets")
    assert score_lead(off, ["Python Developer"], ["python", "django"])[1] < 0.3


def test_guess_company():
    assert guess_company("Acme hiring Python Developer in Mumbai | LinkedIn",
                         "https://in.linkedin.com/jobs/view/1", []) == ("Acme", "")
    name, site = guess_company("Job", "https://www.linkedin.com/x", ["hr@foobar.in"])
    assert name == "Foobar" and site == "https://foobar.in"


def test_aggregator_filter():
    from jobbot.search.base import AGGREGATOR_TITLE, is_non_company
    assert AGGREGATOR_TITLE.search("Mumbai, Maharashtra Python developer Jobs – 431 Open Jobs")
    assert AGGREGATOR_TITLE.search("Python Developer Jobs in Mumbai (2026)")
    assert not AGGREGATOR_TITLE.search("Acme hiring Python Developer in Mumbai")
    assert is_non_company("https://www.simplyhired.co.in/search?q=python")
    assert not is_non_company("https://acme.com/careers")
