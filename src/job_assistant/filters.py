from __future__ import annotations

import re
from datetime import date

from .config import Preferences
from .language import has_explicit_german_requirement
from .models import NormalizedVacancy
from .utils import lower_text

COUNTRY_RE = re.compile(
    r"(?:citizen(?:ship)?(?: of)?|citizen of|authorized to work in|work authorization in|right to work in)"
    r"\s+(?:a\s+)?([A-Z][A-Za-z .-]+)",
    re.IGNORECASE,
)
ADJACENT_ROLE_TITLES = ("analyst", "administrator", "consultant", "specialist")
EXPLICIT_IRRELEVANT_TITLES = ("nurse", "physician", "doctor", "surgeon", "medical assistant")
MAX_MONTHLY_SALARY_RUB = 200_000
RUB_PER_CURRENCY_UNIT = {
    "RUB": 1,
    "RUR": 1,
    "₽": 1,
    "USD": 87,
    "$": 87,
    "GEL": 33,
    "LARI": 33,
    "₾": 33,
}


def apply_role_relevance(vacancy: NormalizedVacancy, preferences: Preferences) -> NormalizedVacancy:
    if _has_early_language_blocker(vacancy):
        return vacancy
    title = vacancy.title or ""
    text = lower_text(vacancy.title, vacancy.excerpt, vacancy.description_text, " ".join(vacancy.categories))
    irrelevant_terms = [*preferences.role_relevance.irrelevant_title_keywords, *EXPLICIT_IRRELEVANT_TITLES]
    explicit_irrelevant = any(contains_phrase(title, term) for term in irrelevant_terms)
    explicit_irrelevant = explicit_irrelevant or any(
        contains_phrase(title, term) for term in preferences.blockers.developer_titles
    )
    explicit_irrelevant = explicit_irrelevant or any(
        contains_phrase(title, term) for term in preferences.blockers.sales_titles
    )
    explicit_irrelevant = explicit_irrelevant or any(
        contains_phrase(title, term) for term in preferences.blockers.support_titles
    )
    if explicit_irrelevant:
        reason = "clearly irrelevant profession before description evaluation"
        vacancy.blocker_reasons = sorted(set([*vacancy.blocker_reasons, reason]))
        vacancy.blocker = True
        vacancy.role_relevance_breakdown = [reason]
    elif _target_title_match(title, preferences):
        vacancy.role_relevance_breakdown = ["title matches configured target title"]
    elif any(contains_phrase(title, term) for term in ADJACENT_ROLE_TITLES) and _has_explicit_analyst_duties(
        text, preferences
    ):
        matched_groups = _matched_strong_groups(text, preferences)
        vacancy.requires_manual_role_review = True
        vacancy.role_relevance_breakdown = [f"adjacent title with explicit BA/SA duties: {', '.join(matched_groups)}"]
    else:
        reason = "title is neither a configured target nor a reviewable adjacent role"
        vacancy.blocker_reasons = sorted(set([*vacancy.blocker_reasons, reason]))
        vacancy.blocker = True
        vacancy.role_relevance_breakdown = [reason]
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


def _has_explicit_analyst_duties(text: str, preferences: Preferences) -> bool:
    groups = set(_matched_strong_groups(text, preferences))
    return bool(groups & {"ba_sa_requirements", "systems_analysis", "atlassian_admin"})


def _is_atlassian_admin(text: str) -> bool:
    platform = any(contains_phrase(text, term) for term in ["jira", "confluence", "atlassian"])
    duty = any(
        contains_phrase(text, term)
        for term in [
            "administration",
            "administer",
            "configuration",
            "configure",
            "workflow",
            "workflows",
            "permission",
            "permissions",
            "scheme",
            "schemes",
            "custom fields",
            "администр",
            "настрой",
            "права",
            "доступ",
        ]
    )
    return platform and duty


def apply_hard_blockers(vacancy: NormalizedVacancy, preferences: Preferences) -> NormalizedVacancy:
    if _has_early_language_blocker(vacancy):
        return vacancy
    text = lower_text(
        vacancy.title,
        vacancy.excerpt,
        vacancy.description_text,
        vacancy.requirements_text,
        " ".join(vacancy.categories),
        vacancy.company,
        " ".join(vacancy.location_restrictions),
    )
    title = (vacancy.title or "").lower()
    reasons: list[str] = [*vacancy.blocker_reasons]
    temporary_company_exclusion = _active_company_exclusion(vacancy.company, preferences)
    if temporary_company_exclusion:
        reasons.append(temporary_company_exclusion)
    low_max_salary = _low_max_salary_reason(vacancy)
    if low_max_salary:
        reasons.append(low_max_salary)
    developer_title = any(contains_phrase(title, term) for term in preferences.blockers.developer_titles)
    developer_title_allowed = any(
        contains_phrase(title, term)
        for term in preferences.blockers.developer_component_keywords
        if term not in {"jira", "confluence", "atlassian"}
    )
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


def _active_company_exclusion(
    company: str | None, preferences: Preferences, *, today: date | None = None
) -> str | None:
    if not company:
        return None
    current_date = today or date.today()
    for exclusion in preferences.blockers.temporary_company_exclusions:
        if current_date <= exclusion.until and contains_phrase(company, exclusion.company):
            return f"company temporarily excluded through {exclusion.until.isoformat()}: {exclusion.company}"
    return None


def _low_max_salary_reason(vacancy: NormalizedVacancy) -> str | None:
    if vacancy.salary_max is None or not vacancy.salary_currency:
        return None
    currency = vacancy.salary_currency.strip().upper()
    rub_rate = RUB_PER_CURRENCY_UNIT.get(currency)
    if rub_rate is None:
        return None
    period = (vacancy.salary_period or "").strip().lower()
    if period in {"year", "annual", "annually"}:
        monthly_max = vacancy.salary_max / 12
    elif period in {"", "month", "monthly"}:
        monthly_max = vacancy.salary_max
    else:
        return None
    monthly_max_rub = monthly_max * rub_rate
    if monthly_max_rub > MAX_MONTHLY_SALARY_RUB:
        return None
    return f"maximum monthly salary is at or below {MAX_MONTHLY_SALARY_RUB:.0f} RUB equivalent"


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
        r"\b(?:удал[её]нн(?:о|ый|ая|ое|ые|ую)|удал[её]нка)\s*(?:по|из|в|для)?\s*"
        r"(?:рф|росси[ия])\b",
        r"\b(?:рф|росси[ия])\s*\(?\s*(?:удал[её]нн(?:о|ый|ая|ое|ые|ую)|удал[её]нка)\b",
    ]
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns)


def apply_filters(vacancies: list[NormalizedVacancy], preferences: Preferences) -> list[NormalizedVacancy]:
    return [apply_hard_blockers(apply_role_relevance(vacancy, preferences), preferences) for vacancy in vacancies]


def _has_early_language_blocker(vacancy: NormalizedVacancy) -> bool:
    return any(reason.startswith("unsupported LinkedIn job-content language:") for reason in vacancy.blocker_reasons)
