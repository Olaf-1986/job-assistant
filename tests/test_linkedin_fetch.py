from __future__ import annotations

from pathlib import Path

import pytest

from job_assistant.config import load_preferences
from job_assistant.linkedin_fetch import (
    LinkedInFetchError,
    LinkedInStopRun,
    _expand_show_more,
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


def test_jsonld_full_description_extraction():
    vacancy = extract_linkedin_vacancy(html("linkedin_job_jsonld.html"), "https://www.linkedin.com/jobs/view/111")

    assert vacancy.job_id == "111"
    assert vacancy.title == "Business Analyst"
    assert vacancy.company == "LinkedIn Acme"
    assert "Jira" in vacancy.description
    assert len(vacancy.description) >= 200


def test_dom_fallback_full_description_extraction():
    vacancy = extract_linkedin_vacancy(html("linkedin_job_dom.html"), "https://www.linkedin.com/jobs/view/222")

    assert vacancy.job_id == "222"
    assert vacancy.title == "System Analyst"
    assert vacancy.company == "DOM Corp"
    assert "API contracts" in vacancy.description


def test_page_state_detection_login_captcha_and_unavailable():
    with pytest.raises(LinkedInStopRun, match="login_required"):
        extract_linkedin_vacancy(html("linkedin_login.html"), "https://www.linkedin.com/login")
    with pytest.raises(LinkedInStopRun, match="captcha"):
        extract_linkedin_vacancy(html("linkedin_captcha.html"), "https://www.linkedin.com/jobs/view/333")
    with pytest.raises(LinkedInFetchError, match="expired"):
        extract_linkedin_vacancy(html("linkedin_unavailable.html"), "https://www.linkedin.com/jobs/view/444")


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
        page_fetcher=lambda url: (html("linkedin_job_jsonld.html"), "Business Analyst | LinkedIn"),
    )

    assert stats["imported"] == 1
    assert read_json(preferences.outputs.output_dir() / preferences.outputs.email_candidates_file) == queue
    assert not (preferences.outputs.output_dir() / preferences.outputs.manual_imports_file).exists()


def test_fetch_imports_and_marks_processed_with_late_keyword_scoring(tmp_path):
    preferences = temp_preferences(tmp_path)
    write_queue(preferences, [linkedin_item("111")])

    stats = fetch_pending_linkedin(
        preferences,
        limit=1,
        dry_run=False,
        pause_seconds=0,
        page_fetcher=lambda url: (html("linkedin_job_jsonld.html"), "Business Analyst | LinkedIn"),
    )

    assert stats["imported"] == 1
    queue = read_json(preferences.outputs.output_dir() / preferences.outputs.email_candidates_file)
    assert queue[0]["status"] == "processed"
    assert queue[0]["pipeline_outcome"] == "shortlisted"
    assert queue[0]["score"] >= 20
    combined = read_json(preferences.outputs.output_dir() / preferences.outputs.combined_json_file)
    assert "OpenAPI" in combined[0]["description_text"]
    assert "openapi" in combined[0]["matched_signals"]


def test_duplicate_job_id_is_idempotent(tmp_path):
    preferences = temp_preferences(tmp_path)
    write_queue(preferences, [linkedin_item("111")])

    def fetcher(url):
        return html("linkedin_job_jsonld.html"), "Business Analyst | LinkedIn"

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
        return html("linkedin_job_jsonld.html"), "Business Analyst | LinkedIn"

    with pytest.raises(LinkedInStopRun):
        fetch_pending_linkedin(preferences, limit=2, dry_run=False, pause_seconds=0, page_fetcher=fetcher)

    queue = read_json(preferences.outputs.output_dir() / preferences.outputs.email_candidates_file)
    assert queue[0]["status"] == "processed"
    assert queue[1]["status"] == "pending"
