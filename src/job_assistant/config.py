from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, ValidationError, field_validator

from .utils import CONFIG_PATH, ROOT


class RunConfig(BaseModel):
    shortlist_size: int = Field(gt=0, le=500)
    pages_per_query: int = Field(gt=0, le=20)
    request_timeout_seconds: float = Field(gt=0, le=120)
    request_delay_seconds: float = Field(ge=0, le=10)
    max_retries: int = Field(ge=0, le=10)


class GenericSourceConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    enabled: bool
    mode: str = "api_feed"
    manual_run_only: bool = False
    requires_env: list[str] = Field(default_factory=list)


class FutureLlmConfig(BaseModel):
    max_analyzed_jobs_per_day: int = Field(gt=0)
    enabled: bool


class JobicySourceConfig(BaseModel):
    enabled: bool
    mode: str = "api_feed"
    endpoint: HttpUrl
    count: int = Field(gt=0, le=100)
    user_agent: str = Field(min_length=3)


class HeadHunterSampleConfig(BaseModel):
    enabled: bool
    name: str = Field(min_length=1)
    host: str = "hh.ru"
    area: int | None = None
    areas: list[int] = Field(default_factory=list)
    schedule: str | None = None
    work_formats: list[str] = Field(default_factory=list)
    search_field: str = "name"


class HeadHunterSourceConfig(BaseModel):
    enabled: bool
    mode: str = "api_search"
    endpoint: HttpUrl
    per_page: int = Field(gt=0, le=100)
    user_agent: str = Field(min_length=3)
    samples: list[HeadHunterSampleConfig] = Field(min_length=1)
    require_authentication: bool = False
    access_token_env: str = "HH_ACCESS_TOKEN"
    user_agent_env: str = "HH_USER_AGENT"
    search_strings: list[str] = Field(default_factory=list)


class TelegramSourceConfig(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    enabled: bool
    mode: str = "user_api_read_only"
    manual_run_only: bool = True
    requires_env: list[str] = Field(default_factory=lambda: ["TELEGRAM_API_ID", "TELEGRAM_API_HASH", "TELEGRAM_PHONE"])
    session_path_env: str = "TELEGRAM_SESSION_PATH"
    default_session_path: str = "data/telegram_sessions/job_assistant"
    core_sources: list[str] = Field(alias="telegram_core_sources", min_length=1)
    trial_sources: list[str] = Field(alias="telegram_trial_sources", default_factory=list)
    excluded_sources: list[str] = Field(alias="telegram_excluded_sources", default_factory=list)
    initial_since_days: int = Field(default=3, ge=1, le=90)
    edit_lookback_hours: int = Field(default=12, ge=1, le=168)
    source_pause_seconds: float = Field(default=1.0, ge=0.25, le=30)
    max_flood_wait_seconds: int = Field(default=300, ge=1, le=3600)

    @field_validator("core_sources", "trial_sources", "excluded_sources")
    @classmethod
    def normalize_source_names(cls, values: list[str]) -> list[str]:
        cleaned = [value.strip().lstrip("@").lower() for value in values if value and value.strip()]
        if len(cleaned) != len(values):
            raise ValueError("Telegram source lists must not contain empty values")
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("Telegram source lists must not contain duplicates")
        return cleaned

    @property
    def enabled_sources(self) -> list[str]:
        excluded = set(self.excluded_sources)
        configured = [*self.core_sources, *self.trial_sources]
        return list(dict.fromkeys(source for source in configured if source not in excluded))


class SourcesConfig(BaseModel):
    jobicy: JobicySourceConfig
    headhunter: HeadHunterSourceConfig
    jooble: GenericSourceConfig
    arbeitnow: GenericSourceConfig
    greenhouse: GenericSourceConfig
    lever: GenericSourceConfig
    email: GenericSourceConfig
    linkedin: GenericSourceConfig
    telegram: TelegramSourceConfig


class QueriesConfig(BaseModel):
    primary: list[str] = Field(min_length=1)
    primary_ru: list[str] = Field(default_factory=list)
    low_priority: list[str] = Field(default_factory=list)

    @field_validator("primary", "primary_ru", "low_priority")
    @classmethod
    def no_empty_values(cls, values: list[str]) -> list[str]:
        cleaned = [value.strip() for value in values if value and value.strip()]
        if len(cleaned) != len(values):
            raise ValueError("query/title lists must not contain empty values")
        return cleaned


class LanguagesConfig(BaseModel):
    accepted: list[str] = Field(min_length=1)
    russian_penalty: int


class LocationConfig(BaseModel):
    onsite_hybrid_allowed_cities: list[str] = Field(min_length=1)
    remote_keywords: list[str] = Field(default_factory=list)
    onsite_keywords: list[str] = Field(default_factory=list)
    hybrid_keywords: list[str] = Field(default_factory=list)


class TemporaryCompanyExclusion(BaseModel):
    company: str = Field(min_length=1)
    until: date

    @field_validator("company")
    @classmethod
    def normalize_company(cls, value: str) -> str:
        return value.strip()


class BlockersConfig(BaseModel):
    developer_titles: list[str] = Field(default_factory=list)
    developer_component_keywords: list[str] = Field(default_factory=list)
    sales_titles: list[str] = Field(default_factory=list)
    support_titles: list[str] = Field(default_factory=list)
    non_blocking_work_auth_countries: list[str] = Field(default_factory=list)
    work_auth_patterns: list[str] = Field(default_factory=list)
    temporary_company_exclusions: list[TemporaryCompanyExclusion] = Field(default_factory=list)


class RoleRelevanceConfig(BaseModel):
    relevant_title_keywords: list[str] = Field(min_length=1)
    irrelevant_title_keywords: list[str] = Field(default_factory=list)
    signal_groups: dict[str, list[str]] = Field(default_factory=dict)


class KeywordGroupConfig(BaseModel):
    weight: int
    keywords: list[str] = Field(min_length=1)


class ScoringConfig(BaseModel):
    title_adjustments: dict[str, int] = Field(default_factory=dict)
    keyword_groups: dict[str, KeywordGroupConfig] = Field(min_length=1)


class OutputsConfig(BaseModel):
    directory: str
    raw_file: str
    normalized_file: str
    csv_file: str
    shortlist_file: str
    blocked_file: str
    role_review_file: str = "role_review.md"
    summary_file: str
    combined_json_file: str = "combined_jobs.json"
    combined_csv_file: str = "combined_jobs.csv"
    combined_shortlist_file: str = "combined_shortlist.md"
    source_status_file: str = "source_status.json"
    manual_imports_file: str = "manual_imports.json"
    email_candidates_file: str = "email_candidates.json"
    linkedin_email_queue_file: str = "linkedin_email_queue.md"
    email_state_file: str = "email_state.json"
    telegram_raw_file: str = "telegram_raw.json"
    telegram_checkpoint_file: str = "telegram_checkpoints.json"
    telegram_failures_file: str = "telegram_failures.json"
    telegram_audit_file: str = "telegram_audit.json"
    telegram_shortlist_file: str = "shortlist_tg.md"

    def output_dir(self) -> Path:
        return ROOT / self.directory


class Preferences(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run: RunConfig
    future_llm: FutureLlmConfig
    sources: SourcesConfig
    queries: QueriesConfig
    languages: LanguagesConfig
    location: LocationConfig
    blockers: BlockersConfig
    role_relevance: RoleRelevanceConfig
    scoring: ScoringConfig
    outputs: OutputsConfig

    @property
    def all_queries(self) -> list[str]:
        return [*self.queries.primary, *self.queries.primary_ru, *self.queries.low_priority]


class CompanyEntry(BaseModel):
    name: str
    enabled: bool = False
    board_token: str | None = None
    site: str | None = None
    region: str = "global"


class TargetCompanies(BaseModel):
    greenhouse: dict[str, list[CompanyEntry]] = Field(default_factory=lambda: {"companies": []})
    lever: dict[str, list[CompanyEntry]] = Field(default_factory=lambda: {"companies": []})


class ConfigLoadError(RuntimeError):
    pass


def load_preferences(path: Path = CONFIG_PATH) -> Preferences:
    try:
        data: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigLoadError(f"Could not read configuration: {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ConfigLoadError(f"Invalid YAML in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigLoadError(f"Configuration must be a YAML mapping: {path}")
    try:
        return Preferences.model_validate(data)
    except ValidationError as exc:
        raise ConfigLoadError(str(exc)) from exc


def load_target_companies(path: Path | None = None) -> TargetCompanies:
    target_path = path or ROOT / "config" / "target_companies.yaml"
    if not target_path.exists():
        return TargetCompanies()
    try:
        data: Any = yaml.safe_load(target_path.read_text(encoding="utf-8")) or {}
        return TargetCompanies.model_validate(data)
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        raise ConfigLoadError(f"Invalid target company configuration: {target_path}: {exc}") from exc
