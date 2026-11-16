from __future__ import annotations

import re

from .config import Preferences


def detect_work_mode(text: str, preferences: Preferences) -> str:
    lowered = text.lower()
    # Work format must not be inferred from remote technology/customers or negation.
    lowered = re.sub(
        r"\b(?:no|not|without)\s+remote(?:\s+(?:work|working|option))?\b|"
        r"\bremote\s+(?:work\s+)?(?:is\s+)?(?:not\s+(?:available|offered|allowed)|unavailable)\b|"
        r"\bremote\s+(?:access|desktop|control|clients?|customers?|servers?|devices?|systems?)\b|"
        r"\bdistributed\s+(?:systems?|databases?|computing|transactions?)\b|"
        r"\bне\s+удал[её]нн\w*\b|\bудал[её]н\w*\s+(?:доступ|клиент\w*|нет|не\s+предусмотрена)\b",
        " ",
        lowered,
    )
    if any(_contains_work_mode_keyword(lowered, keyword) for keyword in preferences.location.hybrid_keywords):
        return "hybrid"
    if any(_contains_work_mode_keyword(lowered, keyword) for keyword in preferences.location.onsite_keywords):
        return "onsite"
    if any(_contains_work_mode_keyword(lowered, keyword) for keyword in preferences.location.remote_keywords):
        return "remote"
    return "unknown"


def _contains_work_mode_keyword(text: str, keyword: str) -> bool:
    keyword = keyword.strip().lower()
    if not keyword:
        return False
    return keyword in text


def detect_allowed_city(text: str, preferences: Preferences) -> str | None:
    lowered = text.lower()
    for city in preferences.location.onsite_hybrid_allowed_cities:
        if city.lower() in lowered:
            return city
    return None


def classify_remote_eligibility(text: str, work_mode: str | None, location: str | None) -> str | None:
    if work_mode != "remote":
        return None
    # Working hours do not establish a country-of-residence restriction.
    timezone_pattern = (
        r"\b(?:EU|EEA|EMEA|Europe|UK|USA?|CIS)\s+(?:(?:working|business)\s+)?"
        r"(?:hours|time\s*zones?)\b"
    )
    text = re.sub(timezone_pattern, "[timezone]", text, flags=re.IGNORECASE)
    location_text = re.sub(timezone_pattern, "[timezone]", location or "", flags=re.IGNORECASE)
    restricted_place = (
        r"(?:EU|EEA|UK|USA?|United States|United Kingdom|Canada|Australia|Cyprus|Spain|Germany|"
        r"France|Poland|Portugal|Italy|Netherlands|Serbia|Armenia|Belarus|Russia|"
        r"Кипр[ае]?|Испани[ия]|Германи[ия]|Серби[ия]|Армени[ия]|Беларус[ьи]|Росси[ия]|РФ|РБ)"
    )
    if re.search(rf"\b{restricted_place}\b", location_text, re.IGNORECASE):
        return "explicit_restricted"
    if re.search(
        r"\b(?:Georgia\s+(?:is\s+)?not\s+(?:supported|eligible|permitted|allowed)|"
        r"(?:work|working)\s+from\s+Georgia\s+(?:is\s+)?not\s+(?:permitted|allowed))\b",
        text,
        re.IGNORECASE,
    ):
        return "explicit_restricted"
    restricted_patterns = (
        # "only from Germany" / "только из России"
        rf"\b(?:only|только)\s+(?:from|in|for|для|из|в)\s+(?:the\s+)?{restricted_place}\b",
        # Common compact Telegram wording: "EU only".
        r"\b(?:EU|EEA|UK|USA?|РФ|Россия)\s+only\b",
        r"\b(?:только)\s+(?:EU|EEA|UK|USA?|РФ|Россия)\b",
        r"\bremote(?:ly)?\s*(?:[-—,:()]|\s)*(?:within|in|from)\s+(?:the\s+)?"
        r"(?:EU|EEA|UK|USA?)\b",
        r"\bremote(?:ly)?\s*[-—,:()]\s*(?:the\s+)?(?:EU|EEA|UK|USA?)\b",
        r"\b(?:EU|EEA|UK|USA?)\s*[-—,:()]?\s+remote\b",
        r"\b(?:удал[её]нн(?:о|ый|ая|ое|ые|ую)|удал[её]нка)\s*(?:по|из|в|для)?\s*"
        r"(?:РФ|РБ|Росси[ия]|Беларус[ьи])\b",
        r"\b(?:РФ|РБ|Росси[ия]|Беларус[ьи])(?:\s*[-/]\s*(?:РФ|РБ|Росси[ия]|Беларус[ьи]))?"
        r"\s*\(?\s*(?:удал[её]нн(?:о|ый|ая|ое|ые|ую)|удал[её]нка)\b",
        # Residency/location requirements without the word "only".
        r"\b(?:must|required\s+to|should)\s+be\s+(?:based|located|resident)\s+in\s+"
        rf"(?:the\s+)?{restricted_place}\b",
        r"\b(?:must|required\s+to)\s+(?:reside|live)\s+in\s+(?:(?:the|an?)\s+)?"
        rf"{restricted_place}\b",
        r"\b(?:нужно|необходимо|долж(?:ен|на|ны))\s+(?:находиться|проживать|быть)\s+в\s+"
        rf"{restricted_place}\b",
    )
    is_restricted = any(re.search(pattern, text, re.IGNORECASE) for pattern in restricted_patterns)
    if is_restricted:
        return "explicit_restricted"
    if re.search(r"\b(?:Tbilisi|Georgia|Тбилиси|Грузи[ия])\b", location_text, re.IGNORECASE) or re.search(
        r"\b(?:only\s+(?:from|in)\s+Georgia|Georgia\s+only|только\s+(?:из|в)\s+Грузи[ия]|"
        r"(?:based|located|resident)\s+in\s+Georgia|(?:находиться|проживать|быть)\s+в\s+Грузии)\b",
        text,
        re.IGNORECASE,
    ):
        return "explicit_allowed_location"
    if re.search(
        r"\b(?:worldwide|anywhere|any country|all countries|по всему миру|из любой страны)\b|"
        r"\b(?:remote(?:ly)?|work|hire|hiring)\s+globally\b|\bglobal\s+remote\b",
        text,
        re.IGNORECASE,
    ):
        return "explicit_global"
    return None
