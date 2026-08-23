from __future__ import annotations

from pathlib import Path

import pytest

from job_assistant.config import load_preferences
from job_assistant.linkedin_fetch import (
    LinkedInFetchError,
    LinkedInPageContent,
    LinkedInStopRun,
    _expand_show_more,
    _main_text_is_ready,
    _wait_for_linkedin_page,
    classify_linkedin_page,
    extract_linkedin_vacancy,
    fetch_pending_linkedin,
)
from job_assistant.utils import read_json, write_json

FIXTURES = Path("tests/fixtures")


def temp_preferences(tmp_path: Path):
    preferences = load_preferences()
    return preferences.model_copy(
        update={"outputs": preferences.outputs.model_copy(update={"directory": str(tmp_path / "output")})}
    )


def html(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def write_queue(preferences, items):
    write_json(preferences.outputs.output_dir() / preferences.outputs.email_candidates_file, items)


def linkedin_item(job_id: str, title: str = "Business Analyst", status: str = "pending") -> dict:
    return {
        "source": "linkedin",
        "external_id": job_id,
        "title": title,
        "company": None,
        "location": None,
        "canonical_url": f"https://www.linkedin.com/jobs/view/{job_id}",
        "received_at": None,
        "message_id": f"m{job_id}",
        "status": status,
    }


def rendered_job_html(title: str = "Business Analyst", company: str = "Example Co") -> str:
    return f"""
    <html>
      <head><title>{title} | {company} | LinkedIn</title></head>
      <body>
        <nav>Skip to search</nav>
        <main>
          <p>{title}</p>
          <p>{title}</p>
          <section>Overview of the position and collaboration context.</section>
          <section>
            <h2>Requirements</h2>
            <p>
              Requirements analysis, stakeholder management, BPMN, REST, JSON, OpenAPI and API contracts are required.
            </p>
            <p>
              This substantive description explains delivery responsibilities, documentation and integration work.
              It includes enough detail for normalization and filtering.
            </p>
          </section>
          <section>People also viewed</section>
          <p>Recommended job that must not be included.</p>
        </main>
      </body>
    </html>
    """


def test_main_text_extraction_works_without_legacy_selectors_or_jsonld():
    vacancy = extract_linkedin_vacancy(
        rendered_job_html(),
        "https://www.linkedin.com/jobs/view/111",
        document_title="Business Analyst | Example Co | LinkedIn",
        candidate_title="Business Analyst",
    )

    assert vacancy.job_id == "111"
    assert vacancy.title == "Business Analyst"
    assert vacancy.company == "Example Co"
    assert vacancy.description.startswith("Requirements")
    assert "OpenAPI" in vacancy.description
    assert "Recommended job" not in vacancy.description
    assert len(vacancy.description) >= 200


def test_repeated_candidate_title_does_not_require_unique_text_match():
    vacancy = extract_linkedin_vacancy(
        rendered_job_html(),
        "https://www.linkedin.com/jobs/view/222",
        document_title="Business Analyst | Example Co | LinkedIn",
        candidate_title="Business Analyst",
    )

    assert vacancy.job_id == "222"
    assert vacancy.title == "Business Analyst"


def test_document_title_fallback_supplies_title_and_company():
    vacancy = extract_linkedin_vacancy(
        rendered_job_html(title="Different page title", company="Fallback Corp"),
        "https://www.linkedin.com/jobs/view/223",
        document_title="Different page title | Fallback Corp | LinkedIn",
        candidate_title="Missing candidate title",
    )

    assert vacancy.title == "Different page title"
    assert vacancy.company == "Fallback Corp"


def test_page_readiness_waits_for_requirements_or_substantive_fallback():
    assert _main_text_is_ready("brief loading shell" * 40) is False
    assert _main_text_is_ready("Requirements\n" + "detail " * 100) is True
    assert _main_text_is_ready("detail " * 500) is True

    class FakePage:
        call = None

        def wait_for_function(self, expression, *, arg, timeout):
            self.call = (expression, arg, timeout)

    page = FakePage()
    _wait_for_linkedin_page(page)

    expression, argument, timeout = page.call
    assert "document.querySelector('main')" in expression
    assert "requirements" in argument["markers"]
    assert timeout == 15_000


def test_page_state_detection_login_captcha_and_unavailable():
    with pytest.raises(LinkedInStopRun, match="login_required"):
        extract_linkedin_vacancy(html("linkedin_login.html"), "https://www.linkedin.com/login")
    with pytest.raises(LinkedInStopRun, match="captcha"):
        extract_linkedin_vacancy(html("linkedin_captcha.html"), "https://www.linkedin.com/jobs/view/333")
    with pytest.raises(LinkedInFetchError, match="expired"):
        extract_linkedin_vacancy(html("linkedin_unavailable.html"), "https://www.linkedin.com/jobs/view/444")


def test_footer_sign_in_does_not_classify_normal_vacancy_as_login():
    vacancy_html = rendered_job_html().replace("</body>", "<footer>Sign in · LinkedIn · Join now</footer></body>")

    vacancy = extract_linkedin_vacancy(
        vacancy_html, "https://www.linkedin.com/jobs/view/555", candidate_title="Business Analyst"
    )

    assert vacancy.job_id == "555"


def test_login_classification_requires_auth_path_or_unambiguous_marker():
    assert classify_linkedin_page("https://www.linkedin.com/jobs/view/555", "Sign in · LinkedIn") is None
    assert classify_linkedin_page("https://www.linkedin.com/authwall?trk=guest", "Please sign in") == "login_required"
    assert classify_linkedin_page("https://www.linkedin.com/uas/login", "Welcome") == "login_required"
    assert classify_linkedin_page("https://www.linkedin.com/jobs/view/555", "Sign in to LinkedIn") == "login_required"


def test_show_more_click_is_attempted():
    clicked = []

    class FakeButton:
        def count(self):
            return 1

        def click(self, timeout):
            clicked.append(timeout)

    class FakeLocator:
        first = FakeButton()

    class FakePage:
        def get_by_role(self, role, name):
            return FakeLocator()

    _expand_show_more(FakePage())

    assert clicked == [2000]


def test_title_prefilter_prevents_navigation(tmp_path):
    preferences = temp_preferences(tmp_path)
    write_queue(preferences, [linkedin_item("111", title="Software Engineer")])

    stats = fetch_pending_linkedin(
        preferences,
        limit=1,
        dry_run=False,
        page_fetcher=lambda url: (_ for _ in ()).throw(AssertionError("should not navigate")),
    )

    assert stats["skipped_title"] == 1
    assert (
        read_json(preferences.outputs.output_dir() / preferences.outputs.email_candidates_file)[0]["pipeline_outcome"]
        == "extraction_failed"
    )


def test_dry_run_produces_no_writes(tmp_path):
    preferences = temp_preferences(tmp_path)
    queue = [linkedin_item("111")]
    write_queue(preferences, queue)

    stats = fetch_pending_linkedin(
        preferences,
        limit=1,
        dry_run=True,
        pause_seconds=0,
        page_fetcher=lambda url: (rendered_job_html(), "Business Analyst | Example Co | LinkedIn"),
    )

    assert stats["imported"] == 1
    assert read_json(preferences.outputs.output_dir() / preferences.outputs.email_candidates_file) == queue
    assert not (preferences.outputs.output_dir() / preferences.outputs.manual_imports_file).exists()


def test_dry_run_uses_candidate_title_and_playwright_main_text(tmp_path):
    preferences = temp_preferences(tmp_path)
    queue = [linkedin_item("112")]
    write_queue(preferences, queue)
    main_text = "\n".join(
        [
            "Business Analyst",
            "Requirements",
            "Requirements analysis, BPMN, OpenAPI and API contracts are required for this role.",
            "The role includes stakeholder management, documentation and integration delivery responsibilities.",
            (
                "It also covers stakeholder workshops, acceptance criteria, process modelling and technical delivery "
                "planning."
            ),
        ]
    )

    stats = fetch_pending_linkedin(
        preferences,
        limit=1,
        dry_run=True,
        pause_seconds=0,
        page_fetcher=lambda url: LinkedInPageContent(
            url, "LinkedIn vacancy body", main_text, "Different title | Example Co | LinkedIn"
        ),
    )

    assert stats["imported"] == 1
    assert read_json(preferences.outputs.output_dir() / preferences.outputs.email_candidates_file) == queue


def test_fetch_imports_and_marks_processed_with_late_keyword_scoring(tmp_path):
    preferences = temp_preferences(tmp_path)
    write_queue(preferences, [linkedin_item("111")])

    stats = fetch_pending_linkedin(
        preferences,
        limit=1,
        dry_run=False,
        pause_seconds=0,
        page_fetcher=lambda url: (rendered_job_html(), "Business Analyst | Example Co | LinkedIn"),
    )

    assert stats["imported"] == 1
    queue = read_json(preferences.outputs.output_dir() / preferences.outputs.email_candidates_file)
    assert queue[0]["status"] == "processed"
    assert queue[0]["pipeline_outcome"] == "shortlisted"
    assert queue[0]["score"] >= 20
    combined = read_json(preferences.outputs.output_dir() / preferences.outputs.combined_json_file)
    manual = read_json(preferences.outputs.output_dir() / preferences.outputs.manual_imports_file)
    assert manual[0]["record"]["__source"] == "linkedin"
    assert "OpenAPI" in combined[0]["description_text"]
    assert "openapi" in combined[0]["matched_signals"]


def test_duplicate_job_id_is_idempotent(tmp_path):
    preferences = temp_preferences(tmp_path)
    write_queue(preferences, [linkedin_item("111")])

    def fetcher(url):
        return rendered_job_html(), "Business Analyst | Example Co | LinkedIn"

    fetch_pending_linkedin(preferences, limit=1, dry_run=False, pause_seconds=0, page_fetcher=fetcher)
    write_queue(preferences, [linkedin_item("111")])
    fetch_pending_linkedin(preferences, limit=1, dry_run=False, pause_seconds=0, page_fetcher=fetcher)

    manual = read_json(preferences.outputs.output_dir() / preferences.outputs.manual_imports_file)
    assert len(manual) == 1
    assert (
        read_json(preferences.outputs.output_dir() / preferences.outputs.email_candidates_file)[0]["status"]
        == "processed"
    )


def test_checkpoint_persists_first_vacancy_before_stop(tmp_path):
    preferences = temp_preferences(tmp_path)
    write_queue(preferences, [linkedin_item("111"), linkedin_item("222")])

    def fetcher(url):
        if url.endswith("/222"):
            raise LinkedInStopRun("login_required")
        return rendered_job_html(), "Business Analyst | Example Co | LinkedIn"

    with pytest.raises(LinkedInStopRun):
        fetch_pending_linkedin(preferences, limit=2, dry_run=False, pause_seconds=0, page_fetcher=fetcher)

    queue = read_json(preferences.outputs.output_dir() / preferences.outputs.email_candidates_file)
    assert queue[0]["status"] == "processed"
    assert queue[1]["status"] == "pending"
