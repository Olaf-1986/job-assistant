from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from job_assistant.config import load_preferences
from job_assistant.linkedin_browser import _route_linkedin_resource
from job_assistant.linkedin_fetch import (
    LinkedInFetchError,
    LinkedInPageContent,
    LinkedInStopRun,
    LinkedInVisibleBlock,
    _expand_show_more,
    _main_text_is_ready,
    _read_linkedin_text,
    _visible_blocks_from_page,
    _wait_for_linkedin_page,
    classify_linkedin_page,
    extract_linkedin_vacancy,
    fetch_pending_linkedin,
)
from job_assistant.linkedin_rate_limit import read_linkedin_rate_limit_state, record_linkedin_rate_limit
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


def linkedin_item(
    job_id: str,
    title: str = "Business Analyst",
    status: str = "pending",
    location: str | None = None,
    received_at: str | None = None,
) -> dict:
    return {
        "source": "linkedin",
        "external_id": job_id,
        "title": title,
        "company": None,
        "location": location,
        "canonical_url": f"https://www.linkedin.com/jobs/view/{job_id}",
        "received_at": received_at,
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


def test_main_text_extraction_keeps_requirements_section_without_legacy_selectors_or_jsonld():
    vacancy = extract_linkedin_vacancy(
        rendered_job_html(),
        "https://www.linkedin.com/jobs/view/111",
        document_title="Business Analyst | Example Co | LinkedIn",
        candidate_title="Business Analyst",
    )

    assert vacancy.job_id == "111"
    assert vacancy.title == "Business Analyst"
    assert vacancy.company == "Example Co"
    assert vacancy.description.startswith("Business Analyst")
    assert "Overview of the position" in vacancy.description
    assert "OpenAPI" in vacancy.description
    assert vacancy.description.index("Overview of the position") < vacancy.description.index("Requirements")
    assert "Recommended job" not in vacancy.description
    assert len(vacancy.description) >= 200


def test_semantic_html_fallback_uses_article_and_excludes_navigation_and_recommendations():
    source = """
    <html><head><title>Business Analyst | Example Co | LinkedIn</title></head><body>
      <nav>Requirements navigation item</nav>
      <article>
        <h1>Business Analyst</h1>
        <p>Overview of the position and collaboration context.</p>
        <h2>Requirements</h2>
        <p>Requirements analysis, stakeholder management, BPMN, REST, JSON and API contracts are required.</p>
        <p>This description contains enough substantive context for filtering and persistence.</p>
        <aside>Recommended jobs and People also viewed</aside>
      </article>
    </body></html>
    """

    vacancy = extract_linkedin_vacancy(
        source,
        "https://www.linkedin.com/jobs/view/227",
        document_title="Business Analyst | Example Co | LinkedIn",
        candidate_title="Business Analyst",
    )

    assert "Requirements analysis" in vacancy.description
    assert "Requirements navigation" not in vacancy.description
    assert "Recommended jobs" not in vacancy.description


def test_no_longer_accepting_marker_in_recommendations_does_not_expire_current_vacancy():
    source = rendered_job_html().replace(
        "Recommended job that must not be included.",
        "This recommended job is no longer accepting applications.",
    )

    vacancy = extract_linkedin_vacancy(
        source,
        "https://www.linkedin.com/jobs/view/228",
        document_title="Business Analyst | Example Co | LinkedIn",
        candidate_title="Business Analyst",
    )

    assert vacancy.job_id == "228"
    assert "no longer accepting" not in vacancy.description


def test_repeated_candidate_title_does_not_require_unique_text_match():
    vacancy = extract_linkedin_vacancy(
        rendered_job_html(),
        "https://www.linkedin.com/jobs/view/222",
        document_title="Business Analyst | Example Co | LinkedIn",
        candidate_title="Business Analyst",
    )

    assert vacancy.job_id == "222"
    assert vacancy.title == "Business Analyst"


def test_requirements_text_is_separate_and_full_description_is_preserved():
    main_text = "\n".join(
        [
            "Business Analyst",
            "Overview of the position and collaboration context.",
            "Requirements",
            "Requirements analysis, stakeholder management and API contracts are required.",
            "Responsibilities",
            "Coordinate delivery and documentation with stakeholders.",
        ]
    )
    blocks = (
        LinkedInVisibleBlock("h1", None, 1, "Business Analyst"),
        LinkedInVisibleBlock("p", None, None, "Overview of the position and collaboration context."),
        LinkedInVisibleBlock("h2", None, 2, "Requirements"),
        LinkedInVisibleBlock(
            "p", None, None, "Requirements analysis, stakeholder management and API contracts are required."
        ),
        LinkedInVisibleBlock("h2", None, 2, "Responsibilities"),
        LinkedInVisibleBlock("p", None, None, "Coordinate delivery and documentation with stakeholders."),
    )

    vacancy = extract_linkedin_vacancy(
        "",
        "https://www.linkedin.com/jobs/view/224",
        document_title="Business Analyst | Example Co | LinkedIn",
        candidate_title="Business Analyst",
        main_text=main_text,
        visible_blocks=blocks,
    )

    assert vacancy.description.startswith("Business Analyst")
    assert vacancy.description.index("Overview") < vacancy.description.index("Requirements")
    assert vacancy.requirements_text is not None
    assert vacancy.requirements_text.startswith("Requirements")
    assert "API contracts" in vacancy.requirements_text
    assert "Responsibilities" not in vacancy.requirements_text


def test_requirements_text_uses_offset_fallback_for_marker_inside_large_text():
    main_text = (
        "Business Analyst Overview and context. "
        "Requirements: stakeholder management, BPMN, REST and API contracts are required. "
        "Responsibilities: coordinate delivery and documentation. "
        "This description is sufficiently substantive for the extractor to retain it. " * 3
    )

    vacancy = extract_linkedin_vacancy(
        "",
        "https://www.linkedin.com/jobs/view/225",
        document_title="Business Analyst | Example Co | LinkedIn",
        candidate_title="Business Analyst",
        main_text=main_text,
    )

    assert vacancy.description.startswith("Business Analyst")
    assert vacancy.requirements_text is not None
    assert vacancy.requirements_text.startswith("Requirements")
    assert "Responsibilities" not in vacancy.requirements_text


def test_requirements_text_is_absent_without_supported_heading_signal():
    main_text = "Business Analyst. " + "General delivery context without a requirements section. " * 8

    vacancy = extract_linkedin_vacancy(
        "",
        "https://www.linkedin.com/jobs/view/226",
        document_title="Business Analyst | Example Co | LinkedIn",
        candidate_title="Business Analyst",
        main_text=main_text,
    )

    assert vacancy.requirements_text is None


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
    assert "querySelectorAll(\"main, [role='main'], article\")" in expression
    assert "requirements" in argument["markers"]
    assert argument["requireSubstantive"] is True
    assert argument["stabilityMs"] == 250
    assert timeout == 15_000

    _wait_for_linkedin_page(page, require_substantive=False)
    assert page.call[1]["requireSubstantive"] is False


def test_page_state_detection_login_captcha_and_unavailable():
    with pytest.raises(LinkedInStopRun, match="login_required"):
        extract_linkedin_vacancy(html("linkedin_login.html"), "https://www.linkedin.com/login")
    with pytest.raises(LinkedInStopRun, match="captcha"):
        extract_linkedin_vacancy(html("linkedin_captcha.html"), "https://www.linkedin.com/jobs/view/333")
    with pytest.raises(LinkedInFetchError, match="expired"):
        extract_linkedin_vacancy(html("linkedin_unavailable.html"), "https://www.linkedin.com/jobs/view/444")
    with pytest.raises(LinkedInStopRun, match="rate_limited"):
        extract_linkedin_vacancy(
            "<html><body><main>Too many requests. Please try again later.</main></body></html>",
            "https://www.linkedin.com/jobs/view/445",
        )


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
    assert classify_linkedin_page("https://www.linkedin.com/jobs/view/555", "Too many requests") == "rate_limited"
    assert (
        classify_linkedin_page(
            "https://www.linkedin.com/jobs/view/555",
            "This employer is no longer accepting applications.",
        )
        == "expired"
    )


@pytest.mark.parametrize(
    ("resource_type", "expected_action"),
    [
        ("image", "abort"),
        ("media", "abort"),
        ("font", "abort"),
        ("document", "continue"),
        ("script", "continue"),
        ("xhr", "continue"),
        ("stylesheet", "continue"),
    ],
)
def test_linkedin_resource_routing_blocks_only_heavy_assets(resource_type, expected_action):
    actions: list[str] = []

    class FakeRequest:
        def __init__(self, kind):
            self.resource_type = kind

    class FakeRoute:
        def __init__(self, kind):
            self.request = FakeRequest(kind)

        def abort(self):
            actions.append("abort")

        def continue_(self):
            actions.append("continue")

    _route_linkedin_resource(FakeRoute(resource_type))

    assert actions == [expected_action]


def test_show_more_click_is_attempted():
    clicked = []

    class FakeButton:
        expanded = False

        def is_visible(self):
            return True

        def get_attribute(self, name):
            assert name == "aria-expanded"
            return "true" if self.expanded else "false"

        def count(self):
            return 1

        def click(self, timeout):
            clicked.append(timeout)
            self.expanded = True

    button = FakeButton()

    class FakeButtons:
        def count(self):
            return 1

        def nth(self, index):
            assert index == 0
            return button

    class FakeContainer:
        def get_by_role(self, role, name):
            assert role == "button"
            return FakeButtons()

    class FakeCandidates:
        def count(self):
            return 1

        def evaluate_all(self, expression):
            return 0

        def nth(self, index):
            assert index == 0
            return FakeContainer()

    class FakePage:
        def locator(self, selector):
            assert selector == "main, [role='main'], article"
            return FakeCandidates()

        def get_by_role(self, role, name):
            raise AssertionError("show-more lookup must be scoped to job content")

    _expand_show_more(FakePage())

    assert clicked == [2000]


def test_visible_block_collection_keeps_semantic_metadata_without_html_output():
    class FakeLocator:
        def evaluate(self, expression):
            assert "querySelectorAll" in expression
            assert "aria-hidden" in expression
            return [
                {"tag": "h2", "role": None, "ariaLevel": 2, "text": "Requirements"},
                {"tag": "div", "role": "heading", "ariaLevel": 2, "text": "Qualifications"},
            ]

    blocks = _visible_blocks_from_page(FakeLocator())

    assert [(block.tag, block.role, block.aria_level, block.text) for block in blocks] == [
        ("h2", None, 2, "Requirements"),
        ("div", "heading", 2, "Qualifications"),
    ]


def test_title_prefilter_prevents_navigation(tmp_path):
    preferences = temp_preferences(tmp_path)
    write_queue(preferences, [linkedin_item("111", title="Software Engineer")])

    stats = fetch_pending_linkedin(
        preferences,
        limit=1,
        dry_run=False,
        page_fetcher=lambda url: (_ for _ in ()).throw(AssertionError("should not navigate")),
    )

    assert stats["title_prefilter_rejected"] == 1
    assert stats["skipped_title"] == 1
    assert (
        read_json(preferences.outputs.output_dir() / preferences.outputs.email_candidates_file)[0]["pipeline_outcome"]
        == "title_prefilter_rejected"
    )


def test_location_prefilter_rejects_explicit_non_tbilisi_onsite_before_navigation(tmp_path):
    preferences = temp_preferences(tmp_path)
    write_queue(preferences, [linkedin_item("113", location="Hybrid · Berlin, Germany")])

    stats = fetch_pending_linkedin(
        preferences,
        limit=1,
        dry_run=False,
        pause_seconds=0,
        location_prefilter=True,
        page_fetcher=lambda url: (_ for _ in ()).throw(AssertionError("should not navigate")),
    )

    assert stats["opened"] == 0
    assert stats["location_prefilter_rejected"] == 1
    assert stats["extraction_failed"] == 0
    record = read_json(preferences.outputs.output_dir() / preferences.outputs.email_candidates_file)[0]
    assert record["pipeline_outcome"] == "location_prefilter_rejected"
    assert record["prefilter_reasons"] == ["explicit onsite or hybrid outside Tbilisi"]


def test_location_prefilter_keeps_emea_remote_and_unknown_locations(tmp_path):
    preferences = temp_preferences(tmp_path)
    queue = [
        linkedin_item("114", location="EMEA"),
        linkedin_item("115", location="Remote - EMEA"),
        linkedin_item("116", location="Berlin, Germany"),
    ]
    write_queue(preferences, queue)
    visited: list[str] = []

    def fetcher(url):
        visited.append(url)
        return rendered_job_html(), "Business Analyst | Example Co | LinkedIn"

    stats = fetch_pending_linkedin(
        preferences,
        limit=3,
        dry_run=True,
        pause_seconds=0,
        location_prefilter=True,
        page_fetcher=fetcher,
    )

    assert stats["location_prefilter_rejected"] == 0
    assert stats["opened"] == 3
    assert [url.rsplit("/", 1)[-1] for url in visited] == ["114", "115", "116"]


def test_default_page_delays_are_between_five_and_twenty_and_not_repeated(tmp_path):
    preferences = temp_preferences(tmp_path)
    write_queue(preferences, [linkedin_item("151"), linkedin_item("152")])
    delays: list[float] = []

    stats = fetch_pending_linkedin(
        preferences,
        limit=2,
        dry_run=True,
        page_fetcher=lambda url: (rendered_job_html(), "Business Analyst | Example Co | LinkedIn"),
        delay_sampler=lambda lower, upper: 7,
        sleeper=delays.append,
    )

    assert stats["opened"] == 2
    assert delays == [7.0, 5.0]
    assert all(5 <= delay <= 20 for delay in delays)


def test_prioritize_puts_tbilisi_remote_and_emea_before_unknown_location(tmp_path):
    preferences = temp_preferences(tmp_path)
    queue = [
        linkedin_item("117", location="EMEA"),
        linkedin_item("118", location="Tbilisi, Georgia"),
        linkedin_item("119", location="Remote - EMEA"),
        linkedin_item("120", location=None),
    ]
    write_queue(preferences, queue)
    visited: list[str] = []

    def fetcher(url):
        visited.append(url)
        return rendered_job_html(), "Business Analyst | Example Co | LinkedIn"

    stats = fetch_pending_linkedin(
        preferences,
        limit=4,
        dry_run=True,
        pause_seconds=0,
        prioritize=True,
        page_fetcher=fetcher,
    )

    assert stats["opened"] == 4
    assert [url.rsplit("/", 1)[-1] for url in visited] == ["118", "119", "117", "120"]


def test_page_extraction_failure_remains_distinct_from_title_prefilter(tmp_path):
    preferences = temp_preferences(tmp_path)
    write_queue(preferences, [linkedin_item("112")])

    stats = fetch_pending_linkedin(
        preferences,
        limit=1,
        dry_run=False,
        pause_seconds=0,
        page_fetcher=lambda url: LinkedInPageContent(
            url, "LinkedIn vacancy body", "too short", "Business Analyst | Example Co | LinkedIn"
        ),
    )

    assert stats["title_prefilter_rejected"] == 0
    assert stats["extraction_failed"] == 1
    assert (
        read_json(preferences.outputs.output_dir() / preferences.outputs.email_candidates_file)[0]["pipeline_outcome"]
        == "extraction_failed"
    )


def test_expired_page_records_no_longer_accepting_blocker(tmp_path):
    preferences = temp_preferences(tmp_path)
    write_queue(preferences, [linkedin_item("153")])

    stats = fetch_pending_linkedin(
        preferences,
        limit=1,
        dry_run=False,
        pause_seconds=0,
        page_fetcher=lambda url: (_ for _ in ()).throw(LinkedInFetchError("expired")),
    )

    assert stats["expired"] == 1
    assert stats["extraction_failed"] == 0
    record = read_json(preferences.outputs.output_dir() / preferences.outputs.email_candidates_file)[0]
    assert record["pipeline_outcome"] == "expired"
    assert record["blocker_reasons"] == ["LinkedIn vacancy is no longer accepting applications"]


def test_rate_limit_stops_immediately_persists_local_block_and_never_retries(tmp_path):
    preferences = temp_preferences(tmp_path)
    write_queue(preferences, [linkedin_item("154"), linkedin_item("155")])
    visited: list[str] = []
    delays: list[float] = []

    def rate_limited_fetcher(url):
        visited.append(url)
        raise LinkedInStopRun("rate_limited", signal="http_429")

    with pytest.raises(LinkedInStopRun, match="resume with a separate run"):
        fetch_pending_linkedin(
            preferences,
            limit=2,
            page_fetcher=rate_limited_fetcher,
            sleeper=delays.append,
        )

    assert len(visited) == 1
    assert delays == []
    state = read_linkedin_rate_limit_state(preferences)
    assert state is not None
    assert state["reason"] == "rate_limited"
    assert state["signal"] == "http_429"
    assert state["active"] is True
    raw_state = read_json(preferences.outputs.output_dir() / "linkedin_rate_limit_state.json")
    assert set(raw_state) == {"reason", "signal", "blocked_at", "retry_after"}

    with pytest.raises(LinkedInStopRun, match="start a separate run after that time"):
        fetch_pending_linkedin(
            preferences,
            limit=2,
            page_fetcher=lambda url: (_ for _ in ()).throw(AssertionError("must not navigate")),
            sleeper=delays.append,
        )

    assert len(visited) == 1
    assert delays == []


def test_local_rate_limit_block_expires_after_two_minutes(tmp_path):
    preferences = temp_preferences(tmp_path)
    blocked_at = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)

    record_linkedin_rate_limit(preferences, signal="page_marker", now=blocked_at)

    still_active = read_linkedin_rate_limit_state(preferences, now=blocked_at + timedelta(seconds=119))
    expired = read_linkedin_rate_limit_state(preferences, now=blocked_at + timedelta(minutes=2))
    assert still_active is not None and still_active["active"] is True
    assert expired is not None and expired["active"] is False


def test_page_text_timeout_becomes_extraction_failure_but_login_timeout_stops_run():
    class FakeLocator:
        def inner_text(self, timeout):
            assert timeout == 5_000
            raise PlaywrightTimeoutError("simulated timeout")

    with pytest.raises(LinkedInFetchError, match="body text did not become readable"):
        _read_linkedin_text(FakeLocator(), "https://www.linkedin.com/jobs/view/121", "body")
    with pytest.raises(LinkedInStopRun, match="login_required"):
        _read_linkedin_text(FakeLocator(), "https://www.linkedin.com/login", "body")


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
    assert combined[0]["requirements_text"].startswith("Requirements")
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
