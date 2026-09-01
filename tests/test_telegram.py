from __future__ import annotations

import asyncio
import inspect
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

from job_assistant.cli import app
from job_assistant.config import Preferences, load_preferences
from job_assistant.deduplicate import deduplicate_vacancies
from job_assistant.filters import apply_filters
from job_assistant.normalize import normalize_records
from job_assistant.paths import output_paths
from job_assistant.persistence import rebuild_from_authoritative_sources
from job_assistant.scoring import score_vacancies
from job_assistant.telegram_audit import audit_telegram
from job_assistant.telegram_client import (
    TelegramAuthorizationError,
    TelegramConnectionError,
    TelegramCredentials,
    TelegramError,
    TelegramMessage,
    TelethonReader,
    _prepare_session_directory,
    _render_qr_terminal,
    _secure_session_files,
    _wait_for_qr_scan,
    telegram_login,
)
from job_assistant.telegram_ingestion import TelegramFetchResult, TelegramSourceReport, fetch_telegram
from job_assistant.telegram_parser import parse_telegram_message
from job_assistant.utils import read_json, write_json
from tests.fixtures.headhunter_records import REMOTE_BA
from tests.fixtures.telegram_messages import (
    BASE_TIME,
    EDITED_VACANCY,
    FORWARDED_VACANCY,
    IRRELEVANT_IT_ROLE,
    MULTIPLE_VACANCIES,
    NEWS_MESSAGE,
    ORDINARY_VACANCY,
    ORDINARY_VACANCY_TEXT,
    RESUME_MESSAGE,
    telegram_message,
)

runner = CliRunner()


class FakeTelegramReader:
    def __init__(
        self,
        messages: dict[str, list[TelegramMessage]],
        *,
        failures: dict[str, Exception] | None = None,
    ) -> None:
        self.messages = messages
        self.failures = failures or {}
        self.cutoffs: dict[str, datetime] = {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return None

    async def resolve_joined_sources(self, allowlist: list[str]):
        return {source: source for source in allowlist if source in self.messages or source in self.failures}

    async def read_messages(self, source: str, entity, since: datetime, limit: int | None = None):
        self.cutoffs[source] = since
        if source in self.failures:
            raise self.failures[source]
        records = [message for message in self.messages.get(source, []) if message.published_at >= since]
        records = sorted(records, key=lambda item: (item.published_at, item.message_id), reverse=True)
        return records[:limit] if limit is not None else records


async def no_sleep(seconds: float) -> None:
    return None


def telegram_preferences(tmp_path: Path, sources: list[str] | None = None) -> Preferences:
    preferences = load_preferences()
    telegram = preferences.sources.telegram.model_copy(
        update={
            "core_sources": sources or ["analytics_jobs"],
            "trial_sources": [],
            "excluded_sources": [],
            "source_pause_seconds": 0.25,
        }
    )
    return preferences.model_copy(
        update={
            "sources": preferences.sources.model_copy(update={"telegram": telegram}),
            "outputs": preferences.outputs.model_copy(update={"directory": str(tmp_path / "output")}),
        }
    )


def test_parser_accepts_vacancy_and_rejects_irrelevant_resume_and_news(tmp_path):
    preferences = telegram_preferences(tmp_path)

    ordinary = parse_telegram_message(ORDINARY_VACANCY, preferences)
    irrelevant = parse_telegram_message(IRRELEVANT_IT_ROLE, preferences)
    resume = parse_telegram_message(RESUME_MESSAGE, preferences)
    news = parse_telegram_message(NEWS_MESSAGE, preferences)

    assert ordinary.vacancy_like is True
    assert len(ordinary.candidates) == 1
    assert ordinary.candidates[0].target_role_match is True
    assert ordinary.target_records[0]["title"] == "Business Analyst"
    assert ordinary.target_records[0]["apply_url"] == "https://jobs.example.test/business-analyst"
    assert irrelevant.vacancy_like is True
    assert len(irrelevant.candidates) == 1
    assert irrelevant.candidates[0].target_role_match is False
    assert irrelevant.target_records == []
    assert resume.rejection_reason == "candidate_or_resume"
    assert news.rejection_reason == "news_advertising_or_event"


def test_parser_rejects_unlabeled_candidate_profile_and_course_with_vacancy_hashtag(tmp_path):
    preferences = telegram_preferences(tmp_path)
    candidate = telegram_message(
        """Candidate: Business Analyst
Experience: five years gathering requirements and documenting BPMN processes.
Desired position: remote Business Analyst
Salary expectations: 4000 USD
Contact: https://t.me/example_candidate
""",
        message_id=15,
    )
    course = telegram_message(
        """#vacancy
Vacancy: Business Analyst Course
Company: Example Academy
Location: Remote worldwide
Requirements: interest in BPMN, requirements, Jira, and documentation.
Apply: https://courses.example.test/business-analysis
""",
        message_id=16,
    )

    candidate_result = parse_telegram_message(candidate, preferences)
    course_result = parse_telegram_message(course, preferences)

    assert candidate_result.rejection_reason == "candidate_or_resume"
    assert candidate_result.candidates == []
    assert course_result.rejection_reason == "news_advertising_or_event"
    assert course_result.candidates == []


def test_link_only_and_ambiguous_multi_vacancy_messages_record_parse_failures(tmp_path):
    preferences = telegram_preferences(tmp_path)
    link_only = telegram_message("#vacancy\nVacancy: Business Analyst\nhttps://jobs.example.test/too-short")
    ambiguous = telegram_message(
        "Vacancies: Business Analyst and Systems Analyst. Requirements and responsibilities are available on request."
    )

    link_result = parse_telegram_message(link_only, preferences)
    ambiguous_result = parse_telegram_message(ambiguous, preferences)

    assert link_result.target_records == []
    assert "substantive vacancy description" in (link_result.failure_reason or "")
    assert ambiguous_result.target_records == []
    assert ambiguous_result.failure_reason == "multiple vacancies could not be split reliably"


def test_forwarded_and_edited_metadata_preserve_original_publication_date(tmp_path):
    preferences = telegram_preferences(tmp_path)
    forwarded = parse_telegram_message(FORWARDED_VACANCY, preferences).target_records[0]
    edited = parse_telegram_message(EDITED_VACANCY, preferences).target_records[0]

    assert forwarded["forwarded_from"] == "@original_jobs"
    assert forwarded["channel_username"] == "jobs_it"
    assert edited["published_at"] == BASE_TIME.isoformat()
    assert edited["edited_at"] == (BASE_TIME + timedelta(hours=4)).isoformat()

    vacancy = normalize_records([{"query": "telegram:test", "record": edited}], preferences)[0]
    assert vacancy.publication_date == BASE_TIME
    assert vacancy.source_metadata["edited_at"] == (BASE_TIME + timedelta(hours=4)).isoformat()


def test_remote_worldwide_is_allowed_and_non_tbilisi_onsite_is_blocked(tmp_path):
    preferences = telegram_preferences(tmp_path)
    berlin_text = ORDINARY_VACANCY_TEXT.replace("Remote worldwide", "Onsite, Berlin")
    tbilisi_text = ORDINARY_VACANCY_TEXT.replace("Remote worldwide", "Hybrid, Tbilisi")
    raw_records = [
        {
            "query": "telegram:test",
            "record": parse_telegram_message(message, preferences).target_records[0],
        }
        for message in (
            ORDINARY_VACANCY,
            telegram_message(berlin_text, message_id=81),
            telegram_message(tbilisi_text, message_id=82),
        )
    ]

    remote, berlin, tbilisi = apply_filters(normalize_records(raw_records, preferences), preferences)

    assert remote.work_mode == "remote"
    assert remote.international_remote_eligibility == "explicit_global"
    assert remote.blocker is False
    assert berlin.work_mode == "onsite"
    assert "onsite or hybrid outside Tbilisi" in berlin.blocker_reasons
    assert tbilisi.work_mode == "hybrid"
    assert tbilisi.detected_city == "Tbilisi"
    assert tbilisi.blocker is False


def test_remote_regional_restrictions_are_blocked_deterministically(tmp_path):
    preferences = telegram_preferences(tmp_path)
    restricted_texts = (
        ORDINARY_VACANCY_TEXT.replace("Remote worldwide", "Remote, EU only"),
        ORDINARY_VACANCY_TEXT.replace("Remote worldwide", "Remote; must be based in Germany"),
        ORDINARY_VACANCY_TEXT.replace("Remote worldwide", "Remote — Europe"),
        ORDINARY_VACANCY_TEXT.replace("Remote worldwide", "Remote EU"),
        ORDINARY_VACANCY_TEXT.replace("Remote worldwide", "Cyprus (Remote)"),
        ORDINARY_VACANCY_TEXT.replace("Remote worldwide", "удалённо по РФ"),
        ORDINARY_VACANCY_TEXT.replace("Remote worldwide", "РФ-РБ (удаленно)"),
    )

    vacancies = apply_filters(
        normalize_records(
            [
                {
                    "query": "telegram:test",
                    "record": parse_telegram_message(
                        telegram_message(text, message_id=90 + index), preferences
                    ).target_records[0],
                }
                for index, text in enumerate(restricted_texts)
            ],
            preferences,
        ),
        preferences,
    )

    assert all(vacancy.work_mode == "remote" for vacancy in vacancies)
    assert all(vacancy.international_remote_eligibility == "explicit_restricted" for vacancy in vacancies)
    assert all("international remote eligibility restricted" in vacancy.blocker_reasons for vacancy in vacancies)


def test_remote_restriction_wins_over_unrelated_global_company_wording(tmp_path):
    preferences = telegram_preferences(tmp_path)
    text = ORDINARY_VACANCY_TEXT.replace(
        "Company: Example Systems", "Company: Example Systems\nWe are a global company"
    ).replace("Remote worldwide", "Remote, EU only")

    record = parse_telegram_message(telegram_message(text, message_id=93), preferences).target_records[0]

    assert record["international_remote_eligibility"] == "explicit_restricted"


def test_unknown_telegram_work_mode_requires_manual_location_review(tmp_path):
    preferences = telegram_preferences(tmp_path)
    text = ORDINARY_VACANCY_TEXT.replace("Location: Remote worldwide\n", "")
    record = parse_telegram_message(telegram_message(text, message_id=98), preferences).target_records[0]

    vacancy = apply_filters(
        normalize_records([{"query": "telegram:test", "record": record}], preferences), preferences
    )[0]

    assert record["requires_manual_location_review"] is True
    assert vacancy.requires_manual_location_review is True
    assert "manual location review required" in vacancy.blocker_reasons


def test_explicit_remote_option_is_not_misclassified_as_foreign_hybrid(tmp_path):
    preferences = telegram_preferences(tmp_path)
    text = ORDINARY_VACANCY_TEXT.replace("Remote worldwide", "Remote or hybrid, Berlin; remote worldwide")
    record = parse_telegram_message(telegram_message(text, message_id=94), preferences).target_records[0]

    vacancy = apply_filters(
        normalize_records([{"query": "telegram:test", "record": record}], preferences), preferences
    )[0]

    assert vacancy.work_mode == "remote"
    assert vacancy.international_remote_eligibility == "explicit_global"
    assert vacancy.blocker is False


def test_application_contact_handle_becomes_preserved_telegram_url(tmp_path):
    text = ORDINARY_VACANCY_TEXT.replace(
        "Apply: https://jobs.example.test/business-analyst", "Apply: @example_recruiter"
    )
    record = parse_telegram_message(telegram_message(text), telegram_preferences(tmp_path)).target_records[0]

    assert record["apply_url"] == "https://t.me/example_recruiter"


def test_explicit_application_link_is_preferred_over_an_earlier_general_url(tmp_path):
    text = ORDINARY_VACANCY_TEXT.replace(
        "Company: Example Systems", "Company: Example Systems\nWebsite: https://example.test/about"
    )

    record = parse_telegram_message(telegram_message(text), telegram_preferences(tmp_path)).target_records[0]

    assert record["apply_url"] == "https://jobs.example.test/business-analyst"


def test_multiple_vacancies_receive_distinct_stable_indexes(tmp_path):
    result = parse_telegram_message(MULTIPLE_VACANCIES, telegram_preferences(tmp_path))

    assert result.failure_reason is None
    assert [item.record["vacancy_index"] for item in result.candidates] == [0, 1]
    assert [item.record["title"] for item in result.candidates] == ["Business Analyst", "Systems Analyst"]
    normalized = normalize_records(
        [{"query": "telegram:test", "record": item.record} for item in result.candidates],
        telegram_preferences(tmp_path),
    )
    assert [item.source_id for item in normalized] == ["1001:14:0", "1001:14:1"]


def test_telegram_duplicates_merge_across_channels_and_headhunter(tmp_path):
    preferences = telegram_preferences(tmp_path)
    duplicate_text = ORDINARY_VACANCY_TEXT.replace(
        "https://jobs.example.test/business-analyst", "https://hh.ru/vacancy/123"
    ).replace("Example Systems", "Acme")
    first = telegram_message(duplicate_text, source="analytics_jobs", channel_id=1001, message_id=31)
    second = telegram_message(duplicate_text, source="jobs_it", channel_id=1002, message_id=41)
    telegram_records = [
        {"query": "telegram:test", "record": parse_telegram_message(message, preferences).target_records[0]}
        for message in (first, second)
    ]
    headhunter = {"query": "Business Analyst", "record": {"__source": "headhunter", **REMOTE_BA}}
    vacancies = normalize_records([headhunter, *telegram_records], preferences)

    deduped, duplicate_count = deduplicate_vacancies(vacancies)

    assert duplicate_count == 2
    assert len(deduped) == 1
    assert deduped[0].sources == ["headhunter", "telegram"]
    assert deduped[0].source_ids["telegram"] == ["1001:31:0", "1002:41:0"]
    references = deduped[0].source_metadata["source_references"]
    assert {item["channel_username"] for item in references} == {"analytics_jobs", "jobs_it"}
    assert all(item["published_at"] == BASE_TIME.isoformat() for item in references)
    assert all(item["raw_text"] == duplicate_text.strip() for item in references)
    assert all(item["apply_url"] == "https://hh.ru/vacancy/123" for item in references)
    assert deduped[0].application_url == "https://hh.ru/vacancy/123"


def test_duplicate_across_telegram_and_linkedin_uses_application_url(tmp_path):
    preferences = telegram_preferences(tmp_path)
    telegram_text = ORDINARY_VACANCY_TEXT.replace(
        "https://jobs.example.test/business-analyst", "https://www.linkedin.com/jobs/view/111"
    )
    telegram_record = parse_telegram_message(telegram_message(telegram_text), preferences).target_records[0]
    linkedin_record = {
        "__source": "linkedin",
        "source_label": "linkedin",
        "page_url": "https://www.linkedin.com/jobs/view/111",
        "vacancy_title": "Business Analyst",
        "company": "Example Systems",
        "visible_text": ORDINARY_VACANCY_TEXT,
    }
    vacancies = normalize_records(
        [
            {"query": "telegram:test", "record": telegram_record},
            {"query": "manual_capture", "record": linkedin_record},
        ],
        preferences,
    )

    deduped, duplicate_count = deduplicate_vacancies(vacancies)

    assert duplicate_count == 1
    assert deduped[0].sources == ["linkedin", "telegram"]


def test_ingestion_is_idempotent_and_shared_pipeline_includes_telegram(tmp_path):
    preferences = telegram_preferences(tmp_path)
    reader = FakeTelegramReader({"analytics_jobs": [ORDINARY_VACANCY]})
    now = BASE_TIME + timedelta(days=1)

    first = asyncio.run(fetch_telegram(preferences, reader=reader, now=now, sleep=no_sleep))
    paths = output_paths(preferences)
    raw_first = read_json(paths["telegram_raw"])
    checkpoint_first = read_json(paths["telegram_checkpoints"])
    second = asyncio.run(
        fetch_telegram(
            preferences,
            reader=FakeTelegramReader({"analytics_jobs": [ORDINARY_VACANCY]}),
            now=now,
            sleep=no_sleep,
        )
    )

    assert first.imported_records == second.imported_records == 1
    assert read_json(paths["telegram_raw"]) == raw_first
    assert read_json(paths["telegram_checkpoints"]) == checkpoint_first
    combined = read_json(paths["combined_json"])
    assert len(combined) == 1
    assert combined[0]["source"] == "telegram"
    assert combined[0]["source_id"] == "1001:10:0"


def test_edited_rerun_updates_content_without_overwriting_published_at(tmp_path):
    preferences = telegram_preferences(tmp_path)
    now = BASE_TIME + timedelta(days=1)
    asyncio.run(
        fetch_telegram(
            preferences,
            reader=FakeTelegramReader({"analytics_jobs": [ORDINARY_VACANCY]}),
            now=now,
            sleep=no_sleep,
        )
    )
    asyncio.run(
        fetch_telegram(
            preferences,
            reader=FakeTelegramReader({"analytics_jobs": [EDITED_VACANCY]}),
            now=now + timedelta(hours=1),
            sleep=no_sleep,
        )
    )

    record = read_json(output_paths(preferences)["telegram_raw"])[0]["record"]
    assert record["published_at"] == BASE_TIME.isoformat()
    assert record["edited_at"] == (BASE_TIME + timedelta(hours=4)).isoformat()
    assert "4000-5000 USD" in record["raw_text"]


def test_dry_run_has_no_project_state_writes(tmp_path):
    preferences = telegram_preferences(tmp_path)

    result = asyncio.run(
        fetch_telegram(
            preferences,
            reader=FakeTelegramReader({"analytics_jobs": [ORDINARY_VACANCY]}),
            now=BASE_TIME + timedelta(days=1),
            dry_run=True,
            sleep=no_sleep,
        )
    )

    assert result.imported_records == 1
    assert not preferences.outputs.output_dir().exists()


def test_all_history_dry_run_selects_global_newest_messages_and_sorts_eligible_targets(tmp_path):
    preferences = telegram_preferences(tmp_path, ["analytics_jobs", "jobs_it"])
    analytics_old = telegram_message(
        ORDINARY_VACANCY_TEXT.replace("Example Systems", "Analytics Old"),
        source="analytics_jobs",
        message_id=501,
        published_at=BASE_TIME,
    )
    analytics_new = telegram_message(
        ORDINARY_VACANCY_TEXT.replace("Example Systems", "Analytics New").replace("business-analyst", "analytics-new"),
        source="analytics_jobs",
        message_id=502,
        published_at=BASE_TIME + timedelta(days=4),
    )
    jobs_old = telegram_message(
        ORDINARY_VACANCY_TEXT.replace("Example Systems", "Jobs Old").replace("business-analyst", "jobs-old"),
        source="jobs_it",
        channel_id=1002,
        message_id=601,
        published_at=BASE_TIME + timedelta(days=2),
    )
    jobs_new = telegram_message(
        ORDINARY_VACANCY_TEXT.replace("Example Systems", "Jobs New").replace("business-analyst", "jobs-new"),
        source="jobs_it",
        channel_id=1002,
        message_id=602,
        published_at=BASE_TIME + timedelta(days=3),
    )
    reader = FakeTelegramReader(
        {
            "analytics_jobs": [analytics_old, analytics_new],
            "jobs_it": [jobs_old, jobs_new],
        }
    )

    result = asyncio.run(
        fetch_telegram(
            preferences,
            reader=reader,
            now=BASE_TIME + timedelta(days=30),
            limit=2,
            dry_run=True,
            all_history=True,
            sleep=no_sleep,
        )
    )

    assert reader.cutoffs == {
        "analytics_jobs": datetime.min.replace(tzinfo=UTC),
        "jobs_it": datetime.min.replace(tzinfo=UTC),
    }
    assert result.messages_inspected == 4
    assert result.messages_read == 2
    assert result.source_reports["analytics_jobs"].messages_read == 1
    assert result.source_reports["jobs_it"].messages_read == 1
    assert [vacancy.company for vacancy in result.eligible_targets] == ["Analytics New", "Jobs New"]
    assert not preferences.outputs.output_dir().exists()


def test_persisted_limited_fetch_is_rejected_before_reading_or_writing(tmp_path):
    preferences = telegram_preferences(tmp_path)

    with pytest.raises(ValueError, match="partial history must not advance checkpoints"):
        asyncio.run(
            fetch_telegram(
                preferences,
                reader=FakeTelegramReader({"analytics_jobs": [ORDINARY_VACANCY]}),
                now=BASE_TIME + timedelta(days=1),
                limit=1,
                sleep=no_sleep,
            )
        )

    assert not preferences.outputs.output_dir().exists()


def test_all_history_requires_dry_run_and_explicit_limit(tmp_path):
    preferences = telegram_preferences(tmp_path)

    with pytest.raises(ValueError, match="requires dry_run and an explicit limit"):
        asyncio.run(
            fetch_telegram(
                preferences,
                reader=FakeTelegramReader({}),
                dry_run=True,
                all_history=True,
                sleep=no_sleep,
            )
        )


def test_telegram_fetch_cli_dry_run_never_updates_status_or_outputs(monkeypatch, tmp_path):
    preferences = telegram_preferences(tmp_path)

    async def fake_fetch(*args, **kwargs):
        return TelegramFetchResult(
            dry_run=True,
            source_reports={
                "analytics_jobs": TelegramSourceReport(
                    source="analytics_jobs",
                    messages_read=1,
                    vacancy_like_messages=1,
                    parsed_vacancies=1,
                    target_role_matches=1,
                    imported_records=1,
                )
            },
        )

    monkeypatch.setattr("job_assistant.cli.load_preferences", lambda: preferences)
    monkeypatch.setattr("job_assistant.cli.fetch_telegram", fake_fetch)
    monkeypatch.setattr(
        "job_assistant.cli.mark_status",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("dry-run status write")),
    )

    result = runner.invoke(app, ["telegram-fetch", "--limit", "5", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert "project state changed: False" in result.output
    assert not preferences.outputs.output_dir().exists()


def test_telegram_fetch_cli_rejects_limit_without_dry_run(monkeypatch):
    monkeypatch.setattr(
        "job_assistant.cli.fetch_telegram",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("fetch must not start")),
    )

    result = runner.invoke(app, ["telegram-fetch", "--limit", "5"])

    assert result.exit_code == 1
    assert "--limit requires --dry-run" in result.output


def test_telegram_fetch_cli_forwards_all_history_and_prints_eligible_preview(monkeypatch, tmp_path):
    preferences = telegram_preferences(tmp_path)
    seen: list[dict[str, object]] = []

    async def fake_fetch(received_preferences, **kwargs):
        assert received_preferences is preferences
        seen.append(kwargs)
        return TelegramFetchResult(dry_run=True)

    monkeypatch.setattr("job_assistant.cli.load_preferences", lambda: preferences)
    monkeypatch.setattr("job_assistant.cli.fetch_telegram", fake_fetch)

    result = runner.invoke(
        app,
        ["telegram-fetch", "--limit", "1000", "--dry-run", "--all-history", "--show-targets"],
    )

    assert result.exit_code == 0, result.output
    assert seen == [
        {
            "since_days": 3,
            "limit": 1000,
            "dry_run": True,
            "all_history": True,
            "ignore_checkpoints": False,
        }
    ]
    assert "Eligible Telegram target vacancies (0)" in result.output
    assert "No eligible target vacancies were found" in result.output


def test_telegram_fetch_cli_accepts_a_sixty_day_dry_run_window(monkeypatch, tmp_path):
    preferences = telegram_preferences(tmp_path)
    seen: list[dict[str, object]] = []

    async def fake_fetch(*args, **kwargs):
        seen.append(kwargs)
        return TelegramFetchResult(dry_run=True)

    monkeypatch.setattr("job_assistant.cli.load_preferences", lambda: preferences)
    monkeypatch.setattr("job_assistant.cli.fetch_telegram", fake_fetch)

    result = runner.invoke(app, ["telegram-fetch", "--since-days", "60", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert seen[0]["since_days"] == 60


def test_telegram_fetch_cli_explicitly_exports_copyable_preview_links(monkeypatch, tmp_path):
    preferences = telegram_preferences(tmp_path)
    parsed = parse_telegram_message(ORDINARY_VACANCY, preferences)
    normalized = normalize_records(
        [{"query": "telegram:analytics_jobs", "record": parsed.target_records[0]}],
        preferences,
    )
    eligible = score_vacancies(apply_filters(normalized, preferences), preferences)

    async def fake_fetch(*args, **kwargs):
        return TelegramFetchResult(dry_run=True, eligible_targets=eligible)

    monkeypatch.setattr("job_assistant.cli.load_preferences", lambda: preferences)
    monkeypatch.setattr("job_assistant.cli.fetch_telegram", fake_fetch)

    result = runner.invoke(app, ["telegram-fetch", "--dry-run", "--export-shortlist"])

    path = preferences.outputs.output_dir() / "shortlist_tg.md"
    assert result.exit_code == 0, result.output
    assert "1 records written" in result.output
    assert path.exists()
    markdown = path.read_text(encoding="utf-8")
    assert "Business Analyst" in markdown
    assert "https://jobs.example.test/business-analyst" in markdown
    assert "Telegram channel: @analytics_jobs" in markdown
    assert "Telegram post: https://t.me/analytics_jobs/10" in markdown


def test_telegram_fetch_cli_rejects_preview_export_without_dry_run(monkeypatch):
    monkeypatch.setattr(
        "job_assistant.cli.fetch_telegram",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("fetch must not start")),
    )

    result = runner.invoke(app, ["telegram-fetch", "--export-shortlist"])

    assert result.exit_code == 1
    assert "--export-shortlist requires --dry-run" in result.output


def test_telegram_fetch_cli_exports_count_ranked_file_for_every_channel(monkeypatch, tmp_path):
    preferences = telegram_preferences(tmp_path, ["analytics_jobs", "jobs_it"])
    parsed = parse_telegram_message(ORDINARY_VACANCY, preferences)
    eligible = score_vacancies(
        apply_filters(
            normalize_records(
                [{"query": "telegram:analytics_jobs", "record": parsed.target_records[0]}],
                preferences,
            ),
            preferences,
        ),
        preferences,
    )

    async def fake_fetch(*args, **kwargs):
        assert kwargs["ignore_checkpoints"] is True
        return TelegramFetchResult(
            dry_run=True,
            source_reports={
                "analytics_jobs": TelegramSourceReport(source="analytics_jobs"),
                "jobs_it": TelegramSourceReport(source="jobs_it"),
            },
            eligible_targets_by_source={"analytics_jobs": eligible, "jobs_it": []},
        )

    monkeypatch.setattr("job_assistant.cli.load_preferences", lambda: preferences)
    monkeypatch.setattr("job_assistant.cli.fetch_telegram", fake_fetch)

    result = runner.invoke(
        app,
        ["telegram-fetch", "--since-days", "30", "--dry-run", "--export-channel-check"],
    )

    directories = list(preferences.outputs.output_dir().glob("telegram_channel_check_*_30d"))
    assert result.exit_code == 0, result.output
    assert len(directories) == 1
    assert (directories[0] / "001_0001_analytics_jobs.md").exists()
    assert (directories[0] / "002_0000_jobs_it.md").exists()
    index = (directories[0] / "index.md").read_text(encoding="utf-8")
    assert index.index("@analytics_jobs") < index.index("@jobs_it")


def test_existing_checkpoint_takes_precedence_over_initial_three_day_window(tmp_path):
    preferences = telegram_preferences(tmp_path)
    paths = output_paths(preferences)
    write_json(
        paths["telegram_checkpoints"],
        {
            "analytics_jobs": {
                "last_message_id": 10,
                "last_message_date": BASE_TIME.isoformat(),
            }
        },
    )
    reader = FakeTelegramReader({"analytics_jobs": []})

    asyncio.run(
        fetch_telegram(
            preferences,
            reader=reader,
            now=BASE_TIME + timedelta(days=10),
            dry_run=True,
            sleep=no_sleep,
        )
    )

    assert reader.cutoffs["analytics_jobs"] == BASE_TIME - timedelta(hours=12)


def test_channel_check_can_read_the_complete_requested_window_despite_checkpoint(tmp_path):
    preferences = telegram_preferences(tmp_path)
    paths = output_paths(preferences)
    write_json(
        paths["telegram_checkpoints"],
        {
            "analytics_jobs": {
                "last_message_id": 10,
                "last_message_date": BASE_TIME.isoformat(),
            }
        },
    )
    reader = FakeTelegramReader({"analytics_jobs": []})

    asyncio.run(
        fetch_telegram(
            preferences,
            reader=reader,
            now=BASE_TIME + timedelta(days=10),
            since_days=30,
            dry_run=True,
            ignore_checkpoints=True,
            sleep=no_sleep,
        )
    )

    assert reader.cutoffs["analytics_jobs"] == BASE_TIME - timedelta(days=20)


def test_checkpoint_recovery_after_one_source_interruption(tmp_path):
    preferences = telegram_preferences(tmp_path, ["analytics_jobs", "jobs_it"])
    second_message = telegram_message(
        ORDINARY_VACANCY_TEXT.replace("Example Systems", "Second Systems"),
        source="jobs_it",
        channel_id=1002,
        message_id=20,
    )
    interrupted = FakeTelegramReader(
        {"analytics_jobs": [ORDINARY_VACANCY]},
        failures={"jobs_it": TelegramError("synthetic source interruption")},
    )

    first = asyncio.run(
        fetch_telegram(
            preferences,
            reader=interrupted,
            now=BASE_TIME + timedelta(days=1),
            sleep=no_sleep,
        )
    )
    checkpoints = read_json(output_paths(preferences)["telegram_checkpoints"])
    assert "analytics_jobs" in checkpoints
    assert "jobs_it" not in checkpoints
    assert first.source_reports["jobs_it"].error == "synthetic source interruption"

    asyncio.run(
        fetch_telegram(
            preferences,
            reader=FakeTelegramReader({"analytics_jobs": [ORDINARY_VACANCY], "jobs_it": [second_message]}),
            now=BASE_TIME + timedelta(days=1, hours=1),
            sleep=no_sleep,
        )
    )
    raw = read_json(output_paths(preferences)["telegram_raw"])
    checkpoints = read_json(output_paths(preferences)["telegram_checkpoints"])
    assert len(raw) == 2
    assert set(checkpoints) == {"analytics_jobs", "jobs_it"}


def test_fetch_recovers_from_malformed_legacy_telegram_metadata_without_logging_contents(tmp_path, caplog):
    preferences = telegram_preferences(tmp_path)
    paths = output_paths(preferences)
    write_json(
        paths["telegram_raw"],
        [
            {
                "query": "telegram:legacy",
                "record": {
                    "__source": "telegram",
                    "title": "Legacy malformed record",
                    "channel_id": 1001,
                    "message_id": "not-an-integer",
                    "vacancy_index": "not-an-integer",
                },
            },
            {"record": "synthetic-private-content-must-not-be-logged"},
        ],
    )
    write_json(
        paths["telegram_failures"],
        [
            {"channel_username": "legacy", "channel_id": "bad", "message_id": "bad"},
            "synthetic-private-failure-content",
        ],
    )
    write_json(
        paths["telegram_checkpoints"],
        {
            "analytics_jobs": {
                "last_message_id": "not-an-integer",
                "last_message_date": BASE_TIME.isoformat(),
            }
        },
    )

    with caplog.at_level("WARNING", logger="job_assistant.normalize"):
        asyncio.run(
            fetch_telegram(
                preferences,
                reader=FakeTelegramReader({"analytics_jobs": [ORDINARY_VACANCY]}),
                now=BASE_TIME + timedelta(days=1),
                sleep=no_sleep,
            )
        )

    assert any(
        isinstance(item.get("record"), dict) and item["record"].get("message_id") == 10
        for item in read_json(paths["telegram_raw"])
        if isinstance(item, dict)
    )
    assert read_json(paths["telegram_checkpoints"])["analytics_jobs"]["last_message_id"] == 10
    assert "synthetic-private-content" not in caplog.text
    assert "contents omitted" in caplog.text


def test_audit_reports_pairwise_containment_and_it_job_offers_activity(tmp_path):
    preferences = telegram_preferences(tmp_path, ["analytics_jobs", "it_job_offers"])
    current = BASE_TIME + timedelta(days=1)
    duplicate = telegram_message(
        ORDINARY_VACANCY_TEXT,
        source="it_job_offers",
        channel_id=2001,
        message_id=50,
        published_at=BASE_TIME + timedelta(hours=1),
    )
    reader = FakeTelegramReader({"analytics_jobs": [ORDINARY_VACANCY], "it_job_offers": [duplicate]})

    result = asyncio.run(
        audit_telegram(
            preferences,
            reader=reader,
            now=current,
            sleep=no_sleep,
            persist=False,
        )
    )

    containment = next(
        item
        for item in result.pairwise_containment
        if item.source == "it_job_offers" and item.other_source == "analytics_jobs"
    )
    assert containment.containment == 1.0
    assert result.sources["it_job_offers"].messages_read == 1
    assert result.it_job_offers_assessment["active_in_window"] is True
    assert "keep trial enabled" in result.it_job_offers_assessment["recommendation"]


def test_audit_recommends_review_only_after_two_complete_containment_windows(tmp_path):
    preferences = telegram_preferences(tmp_path, ["analytics_jobs", "it_job_offers"])
    now = BASE_TIME + timedelta(days=1)
    previous_time = now - timedelta(days=4)
    current_text = ORDINARY_VACANCY_TEXT.replace("business-analyst", "current-business-analyst")
    previous_text = ORDINARY_VACANCY_TEXT.replace("business-analyst", "previous-business-analyst")
    messages = {
        "analytics_jobs": [
            telegram_message(current_text, source="analytics_jobs", message_id=101, published_at=BASE_TIME),
            telegram_message(previous_text, source="analytics_jobs", message_id=102, published_at=previous_time),
        ],
        "it_job_offers": [
            telegram_message(
                current_text,
                source="it_job_offers",
                channel_id=2001,
                message_id=201,
                published_at=BASE_TIME + timedelta(hours=1),
            ),
            telegram_message(
                previous_text,
                source="it_job_offers",
                channel_id=2001,
                message_id=202,
                published_at=previous_time + timedelta(hours=1),
            ),
        ],
    }

    result = asyncio.run(
        audit_telegram(
            preferences,
            reader=FakeTelegramReader(messages),
            now=now,
            sleep=no_sleep,
            persist=False,
        )
    )

    assert result.removal_review_candidates == [
        {
            "source": "it_job_offers",
            "contained_by": "analytics_jobs",
            "recommendation": "review removal; no configuration was changed",
        }
    ]
    assert "keep enabled until user review" in result.it_job_offers_assessment["recommendation"]


def test_audit_never_recommends_removal_from_non_three_day_windows(tmp_path):
    preferences = telegram_preferences(tmp_path, ["analytics_jobs", "it_job_offers"])
    now = BASE_TIME + timedelta(days=1)
    previous_time = now - timedelta(days=3)
    messages = {
        "analytics_jobs": [
            telegram_message(ORDINARY_VACANCY_TEXT, message_id=301, published_at=BASE_TIME),
            telegram_message(ORDINARY_VACANCY_TEXT, message_id=302, published_at=previous_time),
        ],
        "it_job_offers": [
            telegram_message(
                ORDINARY_VACANCY_TEXT,
                source="it_job_offers",
                channel_id=2001,
                message_id=401,
                published_at=BASE_TIME + timedelta(hours=1),
            ),
            telegram_message(
                ORDINARY_VACANCY_TEXT,
                source="it_job_offers",
                channel_id=2001,
                message_id=402,
                published_at=previous_time + timedelta(hours=1),
            ),
        ],
    }

    result = asyncio.run(
        audit_telegram(
            preferences,
            since_days=2,
            reader=FakeTelegramReader(messages),
            now=now,
            sleep=no_sleep,
            persist=False,
        )
    )

    assert result.removal_review_candidates == []
    assert "require consecutive three-day windows" in result.it_job_offers_assessment["recommendation"]


def test_telethon_boundary_contains_only_read_operations_and_secret_repr_is_redacted(tmp_path):
    source = inspect.getsource(TelethonReader)
    for forbidden in ("send_message(", "edit_message(", "delete_messages(", "forward_messages(", "JoinChannel"):
        assert forbidden not in source
    assert "iter_dialogs(" in source
    assert "iter_messages(" in source
    assert "get_entity(" not in source

    credentials = TelegramCredentials(
        api_id=12345,
        api_hash="synthetic-api-hash",
        phone="+995555000000",
        session_path=tmp_path / "session",
    )
    representation = repr(credentials)
    assert "12345" not in representation
    assert "synthetic-api-hash" not in representation
    assert "+995555000000" not in representation


def test_telegram_login_displays_delivery_metadata_and_supports_hidden_code_and_2fa(monkeypatch, tmp_path, capsys):
    class SessionPasswordNeededError(Exception):
        pass

    class PhoneCodeInvalidError(Exception):
        pass

    class PhoneCodeExpiredError(Exception):
        pass

    class FakeErrors:
        pass

    FakeErrors.SessionPasswordNeededError = SessionPasswordNeededError
    FakeErrors.PhoneCodeInvalidError = PhoneCodeInvalidError
    FakeErrors.PhoneCodeExpiredError = PhoneCodeExpiredError

    class FakeClient:
        instance = None

        def __init__(self, session_path, api_id, api_hash, **kwargs):
            self.session_path = Path(f"{session_path}.session")
            self.session_path.write_text("synthetic-session-without-real-auth-data", encoding="utf-8")
            self.authorized = False
            self.sign_in_calls = []
            self.receive_updates = kwargs["receive_updates"]
            FakeClient.instance = self

        async def connect(self):
            return None

        async def disconnect(self):
            return None

        async def is_user_authorized(self):
            return self.authorized

        async def send_code_request(self, phone):
            return type(
                "SentCode",
                (),
                {
                    "phone_code_hash": "synthetic-code-hash",
                    "type": type("SentCodeTypeSms", (), {})(),
                    "next_type": type("SentCodeTypeCall", (), {})(),
                    "timeout": 60,
                },
            )()

        async def sign_in(self, **kwargs):
            self.sign_in_calls.append(kwargs)
            if "code" in kwargs:
                raise SessionPasswordNeededError
            self.authorized = True

    credentials = TelegramCredentials(
        api_id=12345,
        api_hash="synthetic-api-hash",
        phone="+995555000000",
        session_path=tmp_path / "telegram" / "job_assistant",
    )
    prompts = iter(["synthetic-code", "synthetic-2fa"])
    monkeypatch.setattr("job_assistant.telegram_client.load_telegram_credentials", lambda preferences: credentials)
    monkeypatch.setattr("job_assistant.telegram_client._telethon_imports", lambda: (FakeClient, FakeErrors))
    monkeypatch.setattr("job_assistant.telegram_client.getpass.getpass", lambda prompt: next(prompts))

    status = asyncio.run(telegram_login(telegram_preferences(tmp_path)))

    assert status == "authorized"
    assert FakeClient.instance.receive_updates is False
    assert FakeClient.instance.sign_in_calls == [
        {
            "phone": "+995555000000",
            "code": "synthetic-code",
            "phone_code_hash": "synthetic-code-hash",
        },
        {"password": "synthetic-2fa"},
    ]
    assert FakeClient.instance.session_path.stat().st_mode & 0o777 == 0o600
    output = capsys.readouterr().out
    assert "delivery_type=sms" in output
    assert "next_delivery_type=call" in output
    assert "retry_timeout=60s" in output
    assert "synthetic-code-hash" not in output


def test_telegram_login_reports_connection_failures_separately(monkeypatch, tmp_path):
    class FakeErrors:
        class SessionPasswordNeededError(Exception):
            pass

        class PhoneCodeInvalidError(Exception):
            pass

        class PhoneCodeExpiredError(Exception):
            pass

    class FakeClient:
        def __init__(self, *args, **kwargs):
            self.disconnected = False

        async def connect(self):
            raise OSError("synthetic network failure")

        async def disconnect(self):
            self.disconnected = True

    credentials = TelegramCredentials(
        api_id=12345,
        api_hash="synthetic-api-hash",
        phone="+995555000000",
        session_path=tmp_path / "telegram" / "job_assistant",
    )
    monkeypatch.setattr("job_assistant.telegram_client.load_telegram_credentials", lambda preferences: credentials)
    monkeypatch.setattr("job_assistant.telegram_client._telethon_imports", lambda: (FakeClient, FakeErrors))

    with pytest.raises(TelegramConnectionError, match="check network connectivity"):
        asyncio.run(telegram_login(telegram_preferences(tmp_path)))


def test_telegram_login_sanitizes_local_session_constructor_failures(monkeypatch, tmp_path):
    class FakeErrors:
        pass

    class FailingClient:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("synthetic secret and filesystem details")

    credentials = TelegramCredentials(
        api_id=12345,
        api_hash="synthetic-api-hash",
        phone="+995555000000",
        session_path=tmp_path / "telegram" / "job_assistant",
    )
    monkeypatch.setattr("job_assistant.telegram_client.load_telegram_credentials", lambda preferences: credentials)
    monkeypatch.setattr("job_assistant.telegram_client._telethon_imports", lambda: (FailingClient, FakeErrors))

    with pytest.raises(TelegramError) as raised:
        asyncio.run(telegram_login(telegram_preferences(tmp_path)))

    assert str(raised.value) == "Telegram local session could not be opened; check session directory permissions"
    assert "synthetic secret" not in str(raised.value)


def test_session_path_with_extension_gets_restricted_without_double_suffix(tmp_path):
    session_path = tmp_path / "telegram" / "job_assistant.session"
    session_path.parent.mkdir()
    artifacts = [session_path, Path(f"{session_path}-journal"), Path(f"{session_path}-wal")]
    for artifact in artifacts:
        artifact.write_text("synthetic session artifact", encoding="utf-8")
        artifact.chmod(0o666)

    _secure_session_files(session_path, strict=True)

    assert all(artifact.stat().st_mode & 0o777 == 0o600 for artifact in artifacts)
    assert not Path(f"{session_path}.session").exists()


def test_existing_insecure_session_parent_is_not_modified(tmp_path):
    existing_parent = tmp_path / "shared"
    existing_parent.mkdir()
    existing_parent.chmod(0o755)

    with pytest.raises(TelegramError, match="permissions are too broad"):
        _prepare_session_directory(existing_parent / "job_assistant")

    assert existing_parent.stat().st_mode & 0o777 == 0o755


def test_qr_renderer_is_local_and_does_not_return_encoded_url():
    encoded_url = "tg://login?token=synthetic-qr-token"

    rendered = _render_qr_terminal(encoded_url)

    assert "synthetic-qr-token" not in rendered
    assert encoded_url not in rendered
    assert "\033[40m  " in rendered
    assert "\033[47m  " in rendered
    assert all(line.endswith("\033[0m") for line in rendered.splitlines())


def test_qr_waiter_is_started_before_the_matrix_is_displayed(monkeypatch):
    events: list[str] = []

    class FakeQRLogin:
        async def wait(self, timeout=None):
            events.append(f"wait:{timeout}")

    monkeypatch.setattr("job_assistant.telegram_client._display_qr", lambda qr_text: events.append(qr_text))

    asyncio.run(_wait_for_qr_scan(FakeQRLogin(), 4.0, "LOCAL-QR-MATRIX"))

    assert events == ["wait:4.0", "LOCAL-QR-MATRIX"]


def test_telegram_login_qr_supports_local_rendering_and_2fa(monkeypatch, tmp_path, capsys):
    class SessionPasswordNeededError(Exception):
        pass

    class PhoneCodeInvalidError(Exception):
        pass

    class PhoneCodeExpiredError(Exception):
        pass

    class FakeErrors:
        pass

    FakeErrors.SessionPasswordNeededError = SessionPasswordNeededError
    FakeErrors.PhoneCodeInvalidError = PhoneCodeInvalidError
    FakeErrors.PhoneCodeExpiredError = PhoneCodeExpiredError

    class FakeQRLogin:
        url = "tg://login?token=synthetic-qr-token"

        async def wait(self, timeout=None):
            raise SessionPasswordNeededError

    class FakeClient:
        instance = None

        def __init__(self, *args, **kwargs):
            self.authorized = False
            self.qr_calls = 0
            self.sign_in_calls = []
            self.receive_updates = kwargs["receive_updates"]
            FakeClient.instance = self

        async def connect(self):
            return None

        async def disconnect(self):
            return None

        async def is_user_authorized(self):
            return self.authorized

        async def qr_login(self):
            self.qr_calls += 1
            return FakeQRLogin()

        async def sign_in(self, **kwargs):
            self.sign_in_calls.append(kwargs)
            self.authorized = True

    credentials = TelegramCredentials(
        api_id=12345,
        api_hash="synthetic-api-hash",
        phone="+995555000000",
        session_path=tmp_path / "telegram" / "job_assistant",
    )
    monkeypatch.setattr("job_assistant.telegram_client.load_telegram_credentials", lambda preferences: credentials)
    monkeypatch.setattr("job_assistant.telegram_client._telethon_imports", lambda: (FakeClient, FakeErrors))
    monkeypatch.setattr("job_assistant.telegram_client._render_qr_terminal", lambda value: "LOCAL-QR-MATRIX")
    monkeypatch.setattr("job_assistant.telegram_client.getpass.getpass", lambda prompt: "synthetic-2fa")

    status = asyncio.run(telegram_login(telegram_preferences(tmp_path), qr=True))

    assert status == "authorized"
    assert FakeClient.instance.receive_updates is True
    assert FakeClient.instance.qr_calls == 1
    assert FakeClient.instance.sign_in_calls == [{"password": "synthetic-2fa"}]
    output = capsys.readouterr().out
    assert "LOCAL-QR-MATRIX" in output
    assert "Settings → Devices → Link Desktop Device → Scan QR Code" in output
    assert "up to 60 seconds" in output
    assert "synthetic-qr-token" not in output
    assert "tg://login" not in output


def test_telegram_login_qr_refreshes_an_expired_token_within_60_seconds(monkeypatch, tmp_path, capsys):
    class SessionPasswordNeededError(Exception):
        pass

    class PhoneCodeInvalidError(Exception):
        pass

    class PhoneCodeExpiredError(Exception):
        pass

    class FakeErrors:
        pass

    FakeErrors.SessionPasswordNeededError = SessionPasswordNeededError
    FakeErrors.PhoneCodeInvalidError = PhoneCodeInvalidError
    FakeErrors.PhoneCodeExpiredError = PhoneCodeExpiredError

    class FakeClient:
        instance = None

        def __init__(self, *args, **kwargs):
            self.authorized = False
            self.qr = None
            FakeClient.instance = self

        async def connect(self):
            return None

        async def disconnect(self):
            return None

        async def is_user_authorized(self):
            return self.authorized

        async def qr_login(self):
            self.qr = FakeQRLogin(self)
            return self.qr

    class FakeQRLogin:
        def __init__(self, client):
            self.client = client
            self.url = "tg://login?token=synthetic-first-token"
            self.wait_calls = []
            self.recreate_calls = 0
            self.expires = datetime.now(UTC) + timedelta(seconds=5)

        async def wait(self, timeout=None):
            self.wait_calls.append(timeout)
            if len(self.wait_calls) == 1:
                raise asyncio.TimeoutError
            self.client.authorized = True

        async def recreate(self):
            self.recreate_calls += 1
            self.url = "tg://login?token=synthetic-second-token"

    credentials = TelegramCredentials(
        api_id=12345,
        api_hash="synthetic-api-hash",
        phone="+995555000000",
        session_path=tmp_path / "telegram" / "job_assistant",
    )
    monkeypatch.setattr("job_assistant.telegram_client.load_telegram_credentials", lambda preferences: credentials)
    monkeypatch.setattr("job_assistant.telegram_client._telethon_imports", lambda: (FakeClient, FakeErrors))
    monkeypatch.setattr("job_assistant.telegram_client._render_qr_terminal", lambda value: "LOCAL-QR-MATRIX")

    status = asyncio.run(telegram_login(telegram_preferences(tmp_path), qr=True))

    assert status == "authorized"
    assert FakeClient.instance.qr.recreate_calls == 1
    assert 0 < FakeClient.instance.qr.wait_calls[0] <= 5
    output = capsys.readouterr().out
    assert "generating a fresh local QR code" in output
    assert "synthetic-first-token" not in output
    assert "synthetic-second-token" not in output


def test_telegram_login_qr_expiration_is_safe_and_does_not_prompt(monkeypatch, tmp_path, capsys):
    class SessionPasswordNeededError(Exception):
        pass

    class PhoneCodeInvalidError(Exception):
        pass

    class PhoneCodeExpiredError(Exception):
        pass

    class FakeErrors:
        pass

    FakeErrors.SessionPasswordNeededError = SessionPasswordNeededError
    FakeErrors.PhoneCodeInvalidError = PhoneCodeInvalidError
    FakeErrors.PhoneCodeExpiredError = PhoneCodeExpiredError

    class FakeQRLogin:
        url = "tg://login?token=synthetic-expired-token"

        async def wait(self, timeout=None):
            raise asyncio.TimeoutError

        async def recreate(self):
            await asyncio.sleep(0.02)

    class FakeClient:
        def __init__(self, *args, **kwargs):
            self.authorized = False

        async def connect(self):
            return None

        async def disconnect(self):
            return None

        async def is_user_authorized(self):
            return self.authorized

        async def qr_login(self):
            return FakeQRLogin()

    credentials = TelegramCredentials(
        api_id=12345,
        api_hash="synthetic-api-hash",
        phone="+995555000000",
        session_path=tmp_path / "telegram" / "job_assistant",
    )
    monkeypatch.setattr("job_assistant.telegram_client.load_telegram_credentials", lambda preferences: credentials)
    monkeypatch.setattr("job_assistant.telegram_client._telethon_imports", lambda: (FakeClient, FakeErrors))
    monkeypatch.setattr("job_assistant.telegram_client._render_qr_terminal", lambda value: "LOCAL-QR-MATRIX")
    monkeypatch.setattr("job_assistant.telegram_client.QR_LOGIN_WINDOW_SECONDS", 0.01)
    monkeypatch.setattr(
        "job_assistant.telegram_client.getpass.getpass",
        lambda prompt: (_ for _ in ()).throw(AssertionError("QR expiration must not prompt for 2FA")),
    )

    with pytest.raises(TelegramAuthorizationError, match="QR code expired"):
        asyncio.run(telegram_login(telegram_preferences(tmp_path), qr=True))
    output = capsys.readouterr().out
    assert "LOCAL-QR-MATRIX" in output
    assert "synthetic-expired-token" not in output


def test_telegram_login_qr_auth_token_expiration_is_safe(monkeypatch, tmp_path):
    class AuthTokenExpiredError(Exception):
        pass

    class SessionPasswordNeededError(Exception):
        pass

    class PhoneCodeInvalidError(Exception):
        pass

    class PhoneCodeExpiredError(Exception):
        pass

    class FakeErrors:
        pass

    FakeErrors.SessionPasswordNeededError = SessionPasswordNeededError
    FakeErrors.PhoneCodeInvalidError = PhoneCodeInvalidError
    FakeErrors.PhoneCodeExpiredError = PhoneCodeExpiredError

    class FakeQRLogin:
        url = "tg://login?token=synthetic-expired-token"

        async def wait(self, timeout=None):
            raise AuthTokenExpiredError

        async def recreate(self):
            await asyncio.sleep(0.02)

    class FakeClient:
        def __init__(self, *args, **kwargs):
            self.authorized = False

        async def connect(self):
            return None

        async def disconnect(self):
            return None

        async def is_user_authorized(self):
            return self.authorized

        async def qr_login(self):
            return FakeQRLogin()

    credentials = TelegramCredentials(
        api_id=12345,
        api_hash="synthetic-api-hash",
        phone="+995555000000",
        session_path=tmp_path / "telegram" / "job_assistant",
    )
    monkeypatch.setattr("job_assistant.telegram_client.load_telegram_credentials", lambda preferences: credentials)
    monkeypatch.setattr("job_assistant.telegram_client._telethon_imports", lambda: (FakeClient, FakeErrors))
    monkeypatch.setattr("job_assistant.telegram_client._render_qr_terminal", lambda value: "LOCAL-QR-MATRIX")
    monkeypatch.setattr("job_assistant.telegram_client.QR_LOGIN_WINDOW_SECONDS", 0.01)

    with pytest.raises(TelegramAuthorizationError, match="QR code expired"):
        asyncio.run(telegram_login(telegram_preferences(tmp_path), qr=True))


def test_telegram_login_cli_qr_option_is_forwarded_without_network(monkeypatch, tmp_path):
    preferences = telegram_preferences(tmp_path)
    seen: list[bool] = []

    async def fake_login(received_preferences, *, qr=False):
        assert received_preferences is preferences
        seen.append(qr)
        return "authorized"

    monkeypatch.setattr("job_assistant.cli.load_preferences", lambda: preferences)
    monkeypatch.setattr("job_assistant.cli.telegram_login", fake_login)

    result = runner.invoke(app, ["telegram-login", "--qr"])

    assert result.exit_code == 0, result.output
    assert seen == [True]


def test_shortlist_rebuild_keeps_hh_and_telegram_in_one_canonical_entry(tmp_path):
    preferences = telegram_preferences(tmp_path)
    paths = output_paths(preferences)
    write_json(paths["raw"], [{"query": "Business Analyst", "record": {"__source": "headhunter", **REMOTE_BA}}])
    telegram_text = ORDINARY_VACANCY_TEXT.replace(
        "https://jobs.example.test/business-analyst", "https://hh.ru/vacancy/123"
    ).replace("Example Systems", "Acme")
    telegram_record = parse_telegram_message(telegram_message(telegram_text), preferences).target_records[0]
    write_json(paths["telegram_raw"], [{"query": "telegram:test", "record": telegram_record}])

    rebuild_from_authoritative_sources(preferences)

    combined = read_json(paths["combined_json"])
    assert len(combined) == 1
    assert combined[0]["sources"] == ["headhunter", "telegram"]
    vacancies = score_vacancies(
        apply_filters(normalize_records(read_json(paths["telegram_raw"]), preferences), preferences), preferences
    )
    assert vacancies[0].blocker is False


def test_shortlist_source_telegram_cli_writes_attributed_view(monkeypatch, tmp_path):
    preferences = telegram_preferences(tmp_path)
    paths = output_paths(preferences)
    telegram_record = parse_telegram_message(ORDINARY_VACANCY, preferences).target_records[0]
    write_json(paths["telegram_raw"], [{"query": "telegram:test", "record": telegram_record}])
    monkeypatch.setattr("job_assistant.cli.load_preferences", lambda: preferences)

    result = runner.invoke(app, ["shortlist", "--source", "telegram"])

    assert result.exit_code == 0, result.output
    assert "Telegram-only shortlist: 1" in result.output
    assert "Business Analyst" in paths["shortlist"].read_text(encoding="utf-8")
    assert "Business Analyst" in paths["telegram_shortlist"].read_text(encoding="utf-8")
