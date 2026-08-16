from __future__ import annotations

import re

from .config import Preferences
from .language import has_explicit_german_requirement
from .models import NormalizedVacancy
from .utils import lower_text


COUNTRY_RE = re.compile(r"(?:citizen(?:ship)?(?: of)?|citizen of|authorized to work in|work authorization in|right to work in)\s+(?:a\s+)?([A-Z][A-Za-z .-]+)", re.IGNORECASE)


def apply_role_relevance(vacancy: NormalizedVacancy, preferences: Preferences) -> NormalizedVacancy:
    title = (vacancy.title or "")
    text = lower_text(vacancy.title, vacancy.excerpt, vacancy.description_text)
    reasons: list[str] = []
    if any(contains_phrase(title, term) for term in preferences.role_relevance.irrelevant_title_keywords):
        reasons.append("clearly irrelevant profession before scoring")
    elif _target_title_match(title, preferences):
        vacancy.role_relevance_breakdown = ["title matches configured target title"]
        return vacancy
    else:
        matched_groups = _matched_strong_groups(text, preferences)
        if _unambiguous_description_role(text, matched_groups):
            vacancy.role_relevance_breakdown = [f"description has unambiguous analyst/admin duties: {', '.join(sorted(set(matched_groups)))}"]
            return vacancy
        reasons.append("manual review required: no target title and description-only relevance is not unambiguous")
    if reasons:
        vacancy.blocker_reasons = sorted(set([*vacancy.blocker_reasons, *reasons]))
        vacancy.blocker = True
        vacancy.role_relevance_breakdown = reasons
    return vacancy


def contains_phrase(text: str, phrase: str) -> bool:
    phrase = phrase.strip().lower()
    if not phrase:
        return False
    escaped = re.escape(phrase).replace(r"\ ", r"\s+")
    return re.search(rf"(?<![\w-]){escaped}(?![\w-])", text.lower(), flags=re.IGNORECASE | re.UNICODE) is not None


def _target_title_match(title: str, preferences: Preferences) -> bool:
    return any(contains_phrase(title, term) for term in preferences.role_relevance.relevant_title_keywords)


def _matched_strong_groups(text: str, preferences: Preferences) -> list[str]:
    matched_groups = []
    for group, keywords in preferences.role_relevance.signal_groups.items():
        if any(contains_phrase(text, keyword) for keyword in keywords):
            matched_groups.append(group)
    if _is_atlassian_admin(text):
        matched_groups.append("atlassian_admin")
    return sorted(set(matched_groups))


def _unambiguous_description_role(text: str, matched_groups: list[str]) -> bool:
    groups = set(matched_groups)
    strong_groups = {"ba_sa_requirements", "systems_analysis", "modeling", "integration_api", "atlassian_admin", "sql_data_analysis", "technical_documentation"}
    return len(groups & strong_groups) >= 2


def _is_atlassian_admin(text: str) -> bool:
    platform = any(contains_phrase(text, term) for term in ["jira", "confluence", "atlassian"])
    duty = any(contains_phrase(text, term) for term in ["administration", "administer", "configuration", "configure", "workflow", "workflows", "permission", "permissions", "scheme", "schemes", "custom fields", "администр", "настрой", "права", "доступ"])
    return platform and duty


def apply_hard_blockers(vacancy: NormalizedVacancy, preferences: Preferences) -> NormalizedVacancy:
    text = lower_text(vacancy.title, vacancy.excerpt, vacancy.description_text, vacancy.company, " ".join(vacancy.location_restrictions))
    title = (vacancy.title or "").lower()
    reasons: list[str] = [*vacancy.blocker_reasons]
    developer_title = any(contains_phrase(title, term) for term in preferences.blockers.developer_titles)
    developer_title_allowed = any(contains_phrase(title, term) for term in preferences.blockers.developer_component_keywords if term not in {"jira", "confluence", "atlassian"})
    if developer_title and not developer_title_allowed:
        reasons.append("pure developer or engineering role")
    if any(contains_phrase(title, term) for term in preferences.blockers.sales_titles):
        reasons.append("pure sales role")
    if any(contains_phrase(title, term) for term in preferences.blockers.support_titles):
        reasons.append("pure customer-support role")
    if vacancy.work_mode in {"onsite", "hybrid"} and not vacancy.detected_city:
        reasons.append("onsite or hybrid outside Tbilisi")
    if vacancy.international_remote_eligibility == "explicit_restricted":
        reasons.append("international remote eligibility restricted")
    if _russia_only_work_location(text):
        reasons.append("work location restricted to Russia")
    if vacancy.requires_manual_location_review:
        reasons.append("manual location review required")
    if has_explicit_german_requirement(text):
        reasons.append("explicit German language requirement")
    country = _foreign_requirement_country(text, preferences)
    if country:
        reasons.append(f"foreign citizenship/work authorization requirement: {country}")
    for allowed in preferences.blockers.non_blocking_work_auth_countries:
        if allowed.lower() in text and any(pattern in text for pattern in preferences.blockers.work_auth_patterns):
            vacancy.warnings.append(f"work authorization mentioned for {allowed}")
    vacancy.blocker_reasons = sorted(set(reasons))
    vacancy.blocker = bool(vacancy.blocker_reasons)
    return vacancy


def _foreign_requirement_country(text: str, preferences: Preferences) -> str | None:
    allowed = {country.lower() for country in preferences.blockers.non_blocking_work_auth_countries}
    for match in COUNTRY_RE.finditer(text):
        country = re.split(r"\s+(?:is|and|or)\b|[,.;]", match.group(1).strip(" ."), maxsplit=1)[0].strip()
        country_l = country.lower()
        words = country_l.split()
        if not words or words[0] in {"a", "an", "the", "this", "that", "your", "required"} or len(words) > 4:
            continue
        if country_l in allowed or country_l in {"russia", "russian federation"}:
            continue
        if country_l and country_l not in {"country", "your country"}:
            return country
    return None


def _russia_only_work_location(text: str) -> bool:
    patterns = [
        r"\b(?:work|remote work|employment|position)\s+(?:only|exclusively)\s+(?:from|in)\s+russia\b",
        r"\bonly\s+(?:candidates|applicants)\s+(?:from|in)\s+russia\b",
        r"\bтолько\s+(?:из|в|на территории)\s+(?:рф|росси[ии])\b",
        r"\bработа\s+только\s+(?:из|в|на территории)\s+(?:рф|росси[ии])\b",
        r"\bудал[её]нн[а-я\s]+только\s+(?:из|в)\s+(?:рф|росси[ии])\b",
    ]
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns)


def apply_filters(vacancies: list[NormalizedVacancy], preferences: Preferences) -> list[NormalizedVacancy]:
    return [apply_hard_blockers(apply_role_relevance(vacancy, preferences), preferences) for vacancy in vacancies]
