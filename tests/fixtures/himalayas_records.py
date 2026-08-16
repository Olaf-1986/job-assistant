from __future__ import annotations


def record(title: str, description: str, **overrides):
    data = {
        "guid": title.lower().replace(" ", "-"),
        "title": title,
        "companyName": "Acme",
        "companySlug": "acme",
        "employmentType": "Full-time",
        "description": description,
        "excerpt": "International remote team",
        "locationRestrictions": ["Remote"],
        "timezoneRestrictions": [],
        "categories": ["Operations"],
        "pubDate": "2026-01-15T00:00:00Z",
        "applicationLink": f"https://example.com/jobs/{title.lower().replace(' ', '-')}",
    }
    data.update(overrides)
    return data


STRONG_JIRA_ADMIN = record(
    "Jira Confluence Administrator",
    "<p>Own Jira administration, workflows, schemes, permissions, custom fields and Confluence spaces.</p>",
)
BUSINESS_ANALYST = record(
    "Business Analyst",
    "<p>Requirements gathering, user stories, acceptance criteria, BPMN and stakeholder management.</p>",
)
PURE_DEVELOPER = record("Software Engineer", "<p>Build backend services in Python.</p>")
ONSITE_OUTSIDE_TBILISI = record("Business Analyst", "<p>Onsite in Berlin office. Requirements analysis.</p>", locationRestrictions=["Berlin, Germany"])
HYBRID_TBILISI = record("Systems Analyst", "<p>Hybrid in Tbilisi, Georgia. Systems analysis and integrations.</p>", locationRestrictions=["Tbilisi, Georgia"])
RUSSIAN_ROLE = record("Системный аналитик", "<p>Тбилиси удаленно. Анализ требований и документация.</p>")
GEORGIAN_AUTH = record("Business Systems Analyst", "<p>Remote role. Work authorization in Georgia is required. Requirements analysis.</p>")
FOREIGN_CITIZENSHIP = record("Business Analyst", "<p>Remote role. Must be a citizen of Germany.</p>")
JUNIOR_ROLE = record("Junior Business Analyst", "<p>Requirements gathering and user stories.</p>")
INTERNSHIP = record("Business Analyst Intern", "<p>Internship with requirements documentation.</p>")
CRYPTO_ROLE = record("Integration Analyst", "<p>Crypto payments integrations, REST, OpenAPI, JSON.</p>")
JIRA_USER = record("Business Analyst", "<p>Use Jira and Confluence for user stories.</p>")
MALFORMED = {"guid": "missing-title", "description": "<p>No title</p>"}
