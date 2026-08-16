from __future__ import annotations

from .config import Preferences
from .models import NormalizedVacancy
from .utils import lower_text
from .filters import contains_phrase


def score_vacancy(vacancy: NormalizedVacancy, preferences: Preferences) -> NormalizedVacancy:
    score = 0
    breakdown: list[str] = []
    signals: list[str] = []
    text = lower_text(vacancy.title, vacancy.excerpt, vacancy.description_text, vacancy.seniority, " ".join(vacancy.categories))
    groups = preferences.scoring.keyword_groups
    jira_admin = _jira_admin_matches(text, groups["jira_admin"].keywords) if "jira_admin" in groups else []
    jira_user = _matches(text, groups["jira_user"].keywords) if "jira_user" in groups else []
    if jira_admin:
        score += groups["jira_admin"].weight
        breakdown.append(f"+{groups['jira_admin'].weight} Jira/Confluence/Atlassian administration")
        signals.extend(jira_admin)
    elif jira_user:
        score += groups["jira_user"].weight
        breakdown.append(f"+{groups['jira_user'].weight} Jira/Confluence/Atlassian user/tool signal")
        signals.extend(jira_user)
    for name, group in groups.items():
        if name in {"jira_admin", "jira_user"}:
            continue
        matches = _matches(text, group.keywords)
        if matches:
            score += group.weight
            sign = "+" if group.weight >= 0 else ""
            breakdown.append(f"{sign}{group.weight} {name.replace('_', ' ')}")
            signals.extend(matches)
    if vacancy.detected_language == "ru" and preferences.languages.russian_penalty:
        penalty = preferences.languages.russian_penalty
        score += penalty
        breakdown.append(f"{penalty} Russian-language vacancy")
    for title_key, adjustment in preferences.scoring.title_adjustments.items():
        title_phrase = title_key.replace("_", " ")
        if contains_phrase(vacancy.title, title_phrase):
            score += adjustment
            sign = "+" if adjustment >= 0 else ""
            breakdown.append(f"{sign}{adjustment} title adjustment: {title_phrase}")
    vacancy.score = score
    vacancy.score_breakdown = breakdown
    vacancy.matched_signals = sorted(set(signals))
    return vacancy


def _matches(text: str, keywords: list[str]) -> list[str]:
    return [keyword for keyword in keywords if contains_phrase(text, keyword)]


def _jira_admin_matches(text: str, keywords: list[str]) -> list[str]:
    platform_present = any(contains_phrase(text, platform) for platform in ["jira", "confluence", "atlassian"])
    exact_admin = [keyword for keyword in keywords if contains_phrase(text, keyword) and any(platform in keyword.lower() for platform in ["jira", "confluence", "atlassian"])]
    if exact_admin:
        return exact_admin
    if not platform_present:
        return []
    admin_terms = [keyword for keyword in keywords if contains_phrase(text, keyword)]
    return admin_terms


def score_vacancies(vacancies: list[NormalizedVacancy], preferences: Preferences) -> list[NormalizedVacancy]:
    return [vacancy if vacancy.blocker else score_vacancy(vacancy, preferences) for vacancy in vacancies]
