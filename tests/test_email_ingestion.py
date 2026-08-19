from __future__ import annotations

from pathlib import Path

from job_assistant.config import load_preferences
from job_assistant.email_ingestion import extract_candidates_from_message, sync_email_alerts, vacancy_url
from job_assistant.models import BatchStats
from job_assistant.persistence import rebuild_from_authoritative_sources
from job_assistant.utils import read_json
from email import message_from_bytes
from email.policy import default
from tests.fixtures.headhunter_records import HEADHUNTER_RESPONSE


FIXTURES = Path("tests/fixtures")


class FakeIMAP:
    instances: list["FakeIMAP"] = []

    def __init__(self, messages: dict[str, bytes]):
        self.messages = messages
        self.commands: list[tuple] = []
        FakeIMAP.instances.append(self)

    def login(self, user, password):
        self.commands.append(("login", user, "<redacted>"))
        return "OK", []

    def select(self, mailbox, readonly=False):
        self.commands.append(("select", mailbox, readonly))
        return "OK", [b""]

    def uid(self, command, *args):
        self.commands.append(("uid", command, *args))
        if command == "SEARCH":
            return "OK", [" ".join(self.messages).encode("ascii")]
        if command == "FETCH":
            uid = str(args[0])
            return "OK", [(b"BODY[]", self.messages[uid])]
        return "NO", []

    def close(self):
        self.commands.append(("close",))

    def logout(self):
        self.commands.append(("logout",))


def temp_preferences(tmp_path: Path):
    preferences = load_preferences()
    return preferences.model_copy(update={"outputs": preferences.outputs.model_copy(update={"directory": str(tmp_path / "output")})})


def write_env(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("\n".join([
        "EMAIL_IMAP_HOST=imap.gmail.com",
        "EMAIL_IMAP_PORT=993",
        "EMAIL_IMAP_USER=user@example.test",
        "EMAIL_IMAP_APP_PASSWORD=app-password",
        "EMAIL_IMAP_MAILBOX=JobAlerts",
    ]), encoding="utf-8")


def fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def test_gmail_multipart_message_extracts_multiple_vacancies_and_filters_urls():
    message = message_from_bytes(fixture("gmail_job_alert_multipart.eml"), policy=default)
    candidates = extract_candidates_from_message(message)

    assert {(item["source"], item["external_id"]) for item in candidates} == {("linkedin", "111"), ("headhunter", "123")}
    linkedin = next(item for item in candidates if item["source"] == "linkedin")
    assert linkedin["canonical_url"] == "https://www.linkedin.com/jobs/view/111"
    assert linkedin["status"] == "pending"
    assert linkedin["message_id"] == "<alert-1@example.test>"


def test_vacancy_url_accepts_only_known_patterns_and_canonicalizes_tracking():
    assert vacancy_url("https://linkedin.com/jobs/view/111?trk=x") == ("linkedin", "111", "https://www.linkedin.com/jobs/view/111")
    assert vacancy_url("https://hh.ru/vacancy/123?utm_source=x") == ("headhunter", "123", "https://hh.ru/vacancy/123")
    assert vacancy_url("https://headhunter.ge/vacancy/123?from=email") == ("headhunter", "123", "https://headhunter.ge/vacancy/123")
    assert vacancy_url("https://example.com/jobs/view/111") is None
    assert vacancy_url("https://evil.test/redirect?url=https://hh.ru/vacancy/555") is None


def test_email_sync_deduplicates_messages_urls_and_is_idempotent(monkeypatch, tmp_path):
    preferences = temp_preferences(tmp_path)
    write_env(tmp_path)
    monkeypatch.setattr("job_assistant.email_ingestion.ROOT", tmp_path)
    messages = {"1": fixture("gmail_job_alert_multipart.eml"), "2": fixture("gmail_job_alert_duplicate.eml")}

    def factory(host, port):
        return FakeIMAP(messages)

    def hh_lookup(vacancy_id: str):
        record = {**HEADHUNTER_RESPONSE["items"][0], "id": vacancy_id}
        return record, BatchStats(requests_made=1, raw_records_received=1)

    first = sync_email_alerts(preferences, since_days=30, imap_factory=factory, hh_lookup=hh_lookup)
    second = sync_email_alerts(preferences, since_days=30, imap_factory=factory, hh_lookup=hh_lookup)

    candidates = read_json(preferences.outputs.output_dir() / preferences.outputs.email_candidates_file)
    assert len(candidates) == 2
    assert first.processed_message_count == 2
    assert first.linkedin_queue_count == 1
    assert second.processed_message_count == 0
    assert second.linkedin_queue_count == 1
    assert len(second.headhunter_raw) == 0
    assert read_json(preferences.outputs.output_dir() / preferences.outputs.email_state_file)["processed_message_ids"] == ["<alert-1@example.test>", "<alert-duplicate@example.test>"]


def test_email_sync_failed_hh_lookup_writes_candidate_status(monkeypatch, tmp_path):
    preferences = temp_preferences(tmp_path)
    write_env(tmp_path)
    monkeypatch.setattr("job_assistant.email_ingestion.ROOT", tmp_path)
    messages = {"3": fixture("gmail_job_alert_hh_failed.eml")}

    def factory(host, port):
        return FakeIMAP(messages)

    def hh_lookup(vacancy_id: str):
        return None, BatchStats(requests_made=1, errors=["mock_hh_lookup_failed"])

    result = sync_email_alerts(preferences, since_days=30, imap_factory=factory, hh_lookup=hh_lookup)

    candidates = read_json(preferences.outputs.output_dir() / preferences.outputs.email_candidates_file)
    assert result.headhunter_raw == []
    assert candidates[0]["status"] == "hh_lookup_failed"
    assert result.stats.errors == ["mock_hh_lookup_failed"]


def test_email_sync_uses_body_peek_and_does_not_change_flags(monkeypatch, tmp_path):
    preferences = temp_preferences(tmp_path)
    write_env(tmp_path)
    monkeypatch.setattr("job_assistant.email_ingestion.ROOT", tmp_path)
    FakeIMAP.instances = []
    messages = {"1": fixture("gmail_job_alert_multipart.eml")}

    def factory(host, port):
        return FakeIMAP(messages)

    sync_email_alerts(preferences, since_days=30, imap_factory=factory, hh_lookup=lambda vacancy_id: (HEADHUNTER_RESPONSE["items"][0], BatchStats()))

    commands = FakeIMAP.instances[0].commands
    assert ("select", "JobAlerts", True) in commands
    assert any(command[:3] == ("uid", "FETCH", "1") and "BODY.PEEK[]" in " ".join(str(part) for part in command) for command in commands)
    assert all(command[1] != "STORE" for command in commands if command and command[0] == "uid")


def test_dry_run_does_not_write_email_outputs(monkeypatch, tmp_path):
    preferences = temp_preferences(tmp_path)
    write_env(tmp_path)
    monkeypatch.setattr("job_assistant.email_ingestion.ROOT", tmp_path)
    messages = {"1": fixture("gmail_job_alert_multipart.eml")}

    result = sync_email_alerts(preferences, since_days=30, dry_run=True, imap_factory=lambda host, port: FakeIMAP(messages), hh_lookup=lambda vacancy_id: (None, BatchStats()))

    assert result.dry_run is True
    assert not (preferences.outputs.output_dir() / preferences.outputs.email_candidates_file).exists()


def test_email_hh_candidates_flow_through_existing_pipeline(monkeypatch, tmp_path):
    preferences = temp_preferences(tmp_path)
    write_env(tmp_path)
    monkeypatch.setattr("job_assistant.email_ingestion.ROOT", tmp_path)
    messages = {"1": fixture("gmail_job_alert_multipart.eml")}

    result = sync_email_alerts(preferences, since_days=30, imap_factory=lambda host, port: FakeIMAP(messages), hh_lookup=lambda vacancy_id: (HEADHUNTER_RESPONSE["items"][0], BatchStats(requests_made=1, raw_records_received=1)))
    rebuild_from_authoritative_sources(preferences, result.stats, headhunter_raw=result.headhunter_raw)

    combined = read_json(preferences.outputs.output_dir() / preferences.outputs.combined_json_file)
    assert any(item["source"] == "headhunter" and item["source_id"] == "123" for item in combined)
    queue = (preferences.outputs.output_dir() / preferences.outputs.linkedin_email_queue_file).read_text(encoding="utf-8")
    assert "pending" in queue
    assert "https://www.linkedin.com/jobs/view/111" in queue


def test_gmail_fixture_linkedin_alert_is_snippet_not_full_description():
    message = message_from_bytes(fixture("gmail_job_alert_multipart.eml"), policy=default)
    linkedin = [item for item in extract_candidates_from_message(message) if item["source"] == "linkedin"]

    assert linkedin
    assert all(item["external_id"] and item["canonical_url"] and item["title"] for item in linkedin)
    assert all(item["status"] == "pending" for item in linkedin)
