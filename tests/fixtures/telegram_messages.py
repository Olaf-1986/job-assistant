from __future__ import annotations

from datetime import UTC, datetime, timedelta

from job_assistant.telegram_client import TelegramMessage

BASE_TIME = datetime(2026, 8, 27, 9, 0, tzinfo=UTC)


def telegram_message(
    text: str,
    *,
    source: str = "analytics_jobs",
    channel_id: int = 1001,
    message_id: int = 10,
    published_at: datetime = BASE_TIME,
    edited_at: datetime | None = None,
    forwarded_from: str | None = None,
    entity_urls: tuple[str, ...] = (),
) -> TelegramMessage:
    return TelegramMessage(
        channel_username=source,
        channel_id=channel_id,
        message_id=message_id,
        message_url=f"https://t.me/{source}/{message_id}",
        text=text,
        published_at=published_at,
        edited_at=edited_at,
        forwarded_from=forwarded_from,
        entity_urls=entity_urls,
    )


ORDINARY_VACANCY_TEXT = """#vacancy
Vacancy: Business Analyst
Company: Example Systems
Location: Remote worldwide
Responsibilities:
- Gather and document business requirements, user stories, and acceptance criteria.
- Model business processes with BPMN and coordinate API integrations.
Requirements:
- Three years of business analysis experience and stakeholder management.
- Jira, Confluence, REST, and technical documentation.
Salary: 3500-4500 USD
Apply: https://jobs.example.test/business-analyst
"""

IRRELEVANT_IT_ROLE_TEXT = """#vacancy
Vacancy: Backend Developer
Company: Example Systems
Location: Remote worldwide
Responsibilities:
- Build and operate Python services and PostgreSQL databases.
Requirements:
- Three years of backend engineering experience.
Apply: https://jobs.example.test/backend-developer
"""

RESUME_TEXT = """#резюме
Ищу работу Business Analyst.
Опыт: пять лет анализа требований, BPMN, Jira и интеграций.
Предпочтительный формат: удаленно.
Контакт: https://t.me/example_candidate
"""

NEWS_TEXT = """Новый бесплатный вебинар для аналитиков
Обсудим карьерные советы, BPMN и документацию. Регистрация на мероприятие открыта.
Подробности: https://events.example.test/analyst-webinar
"""

MULTIPLE_VACANCIES_TEXT = """#vacancies
Company: Multi Example
Location: Remote worldwide
1. Business Analyst
Responsibilities: gather requirements, write user stories, and model processes with BPMN.
Requirements: three years of business analysis and stakeholder management.
Apply: https://jobs.example.test/multi-ba

2. Systems Analyst
Responsibilities: analyze system integrations and document REST API contracts.
Requirements: systems analysis, UML, SQL, and technical documentation experience.
Apply: https://jobs.example.test/multi-sa
"""


ORDINARY_VACANCY = telegram_message(ORDINARY_VACANCY_TEXT)
IRRELEVANT_IT_ROLE = telegram_message(IRRELEVANT_IT_ROLE_TEXT, message_id=11)
RESUME_MESSAGE = telegram_message(RESUME_TEXT, message_id=12)
NEWS_MESSAGE = telegram_message(NEWS_TEXT, message_id=13)
FORWARDED_VACANCY = telegram_message(
    ORDINARY_VACANCY_TEXT,
    source="jobs_it",
    channel_id=1002,
    message_id=20,
    forwarded_from="@original_jobs",
)
EDITED_VACANCY = telegram_message(
    ORDINARY_VACANCY_TEXT.replace("3500-4500 USD", "4000-5000 USD"),
    edited_at=BASE_TIME + timedelta(hours=4),
)
MULTIPLE_VACANCIES = telegram_message(MULTIPLE_VACANCIES_TEXT, message_id=14)
