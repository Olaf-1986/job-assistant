from __future__ import annotations


HEADHUNTER_RESPONSE = {
    "items": [
        {
            "id": "123",
            "name": "Business Analyst",
            "alternate_url": "https://hh.ru/vacancy/123",
            "employer": {"name": "Acme"},
            "area": {"id": "113", "name": "Россия"},
            "schedule": {"id": "remote", "name": "Удаленная работа"},
            "employment": {"id": "full", "name": "Полная занятость"},
            "experience": {"id": "between3And6", "name": "От 3 до 6 лет"},
            "salary": {"from": 3000, "to": 4500, "currency": "USD"},
            "snippet": {
                "requirement": "Requirements gathering, user stories, acceptance criteria, BPMN.",
                "responsibility": "Analyze business processes and document functional requirements.",
            },
            "published_at": "2026-07-30T10:00:00+0300",
        },
        {
            "id": "456",
            "name": "Systems Analyst",
            "alternate_url": "https://hh.ru/vacancy/456",
            "employer": {"name": "Tbilisi Tech"},
            "area": {"id": "2758", "name": "Тбилиси"},
            "schedule": {"id": "flexible", "name": "Гибкий график"},
            "employment": {"id": "full", "name": "Полная занятость"},
            "experience": {"id": "between1And3", "name": "От 1 года до 3 лет"},
            "salary": None,
            "snippet": {
                "requirement": "Systems analysis, REST, JSON, integrations.",
                "responsibility": "Hybrid in Tbilisi. Work with stakeholders and development teams.",
            },
            "published_at": "2026-07-29T10:00:00+0300",
        },
    ]
}

REMOTE_BA = HEADHUNTER_RESPONSE["items"][0]
TBILISI_SYSTEMS_ANALYST = HEADHUNTER_RESPONSE["items"][1]
