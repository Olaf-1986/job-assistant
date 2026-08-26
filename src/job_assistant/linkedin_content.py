from __future__ import annotations

import re
from typing import Sequence

from bs4 import BeautifulSoup

from .linkedin_policy import STOP_REASONS, classify_linkedin_page, has_linkedin_no_longer_accepting_marker
from .linkedin_queue import canonical_linkedin_url, linkedin_job_id
from .linkedin_types import LinkedInFetchError, LinkedInStopRun, LinkedInVacancy, LinkedInVisibleBlock
from .utils import normalize_space

MIN_DESCRIPTION_LENGTH = 200
VACANCY_SECTION_MARKERS = (
    "requirements",
    "must-have",
    "must have",
    "qualifications",
    "prerequisites",
    "требования",
)
REQUIREMENTS_HEADING_MARKERS = (
    *VACANCY_SECTION_MARKERS,
    "what you bring",
    "what you'll bring",
    "your profile",
    "your qualifications",
    "required skills",
    "key qualifications",
    "skills and experience",
    "experience and skills",
    "квалификация",
    "навыки",
    "опыт и навыки",
)
NEXT_SECTION_HEADING_MARKERS = (
    "responsibilities",
    "what you'll do",
    "what you will do",
    "about the role",
    "about the job",
    "benefits",
    "what we offer",
    "about us",
    "duties",
    "обязанности",
    "задачи",
    "условия",
    "о компании",
    "о роли",
)
IRRELEVANT_SECTION_MARKERS = (
    "people also viewed",
    "jobs you may like",
    "recommended jobs",
    "similar jobs",
)
_JOB_CONTENT_SELECTOR = "main, [role='main'], article"
_EXCLUDED_CONTENT_SELECTOR = (
    "nav, footer, aside, form, [role='navigation'], [role='complementary'], [role='contentinfo'], [aria-hidden='true']"
)


def extract_linkedin_vacancy(
    html: str,
    page_url: str,
    document_title: str = "LinkedIn",
    candidate_title: str | None = None,
    main_text: str | None = None,
    visible_blocks: Sequence[LinkedInVisibleBlock] | None = None,
) -> LinkedInVacancy:
    """Normalize page text into full description plus an optional requirements section."""
    soup = BeautifulSoup(html, "html.parser")
    page_text = normalize_space(soup.get_text(" "))
    reason = classify_linkedin_page(page_url, page_text)
    if reason in STOP_REASONS:
        signal = "page_marker" if reason == "rate_limited" else None
        raise LinkedInStopRun(reason, signal=signal)
    job_id = linkedin_job_id(page_url)
    if not job_id:
        raise LinkedInFetchError("extraction_failed: missing LinkedIn job id")

    if main_text is None and visible_blocks:
        main_text = _text_from_visible_blocks(visible_blocks)
    main_text = main_text if main_text is not None else _main_text_from_html(soup)
    if reason == "expired" and not normalize_space(main_text):
        raise LinkedInFetchError("expired")
    title, company = _document_title_parts(document_title)
    candidate_title = normalize_space(candidate_title or "")
    if candidate_title and _contains_normalized(main_text, candidate_title):
        title = candidate_title
    description = _description_from_main_text(main_text)
    if has_linkedin_no_longer_accepting_marker(description):
        raise LinkedInFetchError("expired")
    requirements_text = _requirements_text_from_content(main_text, visible_blocks)
    if not title:
        raise LinkedInFetchError("extraction_failed: missing title")
    if len(description) < MIN_DESCRIPTION_LENGTH:
        raise LinkedInFetchError("extraction_failed: missing substantive description")
    return LinkedInVacancy(
        job_id=job_id,
        canonical_url=canonical_linkedin_url(job_id),
        title=title,
        company=company,
        location=None,
        description=description,
        document_title=document_title,
        requirements_text=requirements_text,
    )


def _contains_normalized(text: str, phrase: str) -> bool:
    return normalize_space(phrase).casefold() in normalize_space(text).casefold()


def _document_title_parts(document_title: str) -> tuple[str, str | None]:
    parts = [normalize_space(part) for part in document_title.split("|") if normalize_space(part)]
    parts = [part for part in parts if part.casefold() != "linkedin"]
    title = parts[0] if parts else ""
    company = parts[1] if len(parts) > 1 else None
    return title, company


def _main_text_from_html(soup: BeautifulSoup) -> str:
    candidates = soup.select(_JOB_CONTENT_SELECTOR)
    main = max(candidates, key=lambda element: len(normalize_space(element.get_text(" "))), default=None)
    if main is None:
        return ""
    for element in main.select(f"{_EXCLUDED_CONTENT_SELECTOR}, button, script, style"):
        element.decompose()
    return main.get_text("\n")


def _text_from_visible_blocks(blocks: Sequence[LinkedInVisibleBlock]) -> str:
    return "\n".join(normalize_space(block.text) for block in blocks if normalize_space(block.text))


def _description_from_main_text(main_text: str) -> str:
    lines: list[str] = []
    seen: set[str] = set()
    for raw_line in main_text.splitlines():
        line = normalize_space(raw_line)
        lowered = line.casefold()
        if not line or lowered in seen:
            continue
        if any(marker in lowered for marker in IRRELEVANT_SECTION_MARKERS):
            break
        if lowered in {"skip to search", "skip to content", "sign in", "join now", "show more", "see more"}:
            continue
        seen.add(lowered)
        lines.append(line)
    return "\n".join(lines)


def _requirements_text_from_content(
    main_text: str,
    visible_blocks: Sequence[LinkedInVisibleBlock] | None,
) -> str | None:
    if visible_blocks:
        semantic_text = _requirements_from_visible_blocks(visible_blocks)
        if semantic_text:
            return semantic_text
    return _requirements_from_text_offset(main_text)


def _requirements_from_visible_blocks(blocks: Sequence[LinkedInVisibleBlock]) -> str | None:
    heading_index = next(
        (index for index, block in enumerate(blocks) if _is_requirements_heading(block)),
        None,
    )
    if heading_index is None:
        return None
    section: list[str] = []
    for index in range(heading_index, len(blocks)):
        block = blocks[index]
        if index > heading_index and _is_next_section_heading(block):
            break
        section.append(block.text)
        if _contains_irrelevant_marker(block.text):
            break
    return _join_content_lines(section)


def _is_requirements_heading(block: LinkedInVisibleBlock) -> bool:
    text = normalize_space(block.text)
    if not text:
        return False
    if block.role == "heading" or block.tag in {"h1", "h2", "h3", "h4", "h5", "h6", "strong", "b"}:
        return _contains_any_marker(text, REQUIREMENTS_HEADING_MARKERS)
    return len(text) <= 140 and _contains_any_marker(text, REQUIREMENTS_HEADING_MARKERS)


def _is_next_section_heading(block: LinkedInVisibleBlock) -> bool:
    text = normalize_space(block.text)
    if not text or not _contains_any_marker(text, NEXT_SECTION_HEADING_MARKERS):
        return False
    if block.role == "heading" or block.tag in {"h1", "h2", "h3", "h4", "h5", "h6", "strong", "b"}:
        return True
    return len(text) <= 140


def _contains_any_marker(text: str, markers: tuple[str, ...]) -> bool:
    lowered = normalize_space(text).casefold()
    return any(marker.casefold() in lowered for marker in markers)


def _contains_irrelevant_marker(text: str) -> bool:
    lowered = normalize_space(text).casefold()
    return any(marker in lowered for marker in IRRELEVANT_SECTION_MARKERS)


def _requirements_from_text_offset(main_text: str) -> str | None:
    text = main_text.strip()
    if not text:
        return None
    lowered = text.casefold()
    starts = [
        match.start()
        for marker in REQUIREMENTS_HEADING_MARKERS
        for match in re.finditer(
            rf"(?<![\w-]){re.escape(marker.casefold())}(?![\w-])",
            lowered,
        )
        if _looks_like_section_start(text, match.start(), match.end())
    ]
    if not starts:
        return None
    start = min(starts)
    end = len(text)
    for marker in (*NEXT_SECTION_HEADING_MARKERS, *IRRELEVANT_SECTION_MARKERS):
        match = re.search(
            rf"(?<![\w-]){re.escape(marker.casefold())}(?![\w-])",
            lowered[start + 1 :],
        )
        if match:
            end = min(end, start + 1 + match.start())
    result = _join_content_lines([text[start:end]])
    return result or None


def _looks_like_section_start(text: str, start: int, end: int) -> bool:
    if start == 0 or text[start - 1] in "\r\n":
        return True
    before = text[:start].rstrip()
    after = text[end:].lstrip()
    return before.endswith((".", ":", ";", "-", "—", "•")) or after.startswith((":", "-", "—", "•"))


def _join_content_lines(lines: list[str]) -> str | None:
    result: list[str] = []
    seen: set[str] = set()
    for raw_line in lines:
        line = normalize_space(raw_line)
        lowered = line.casefold()
        if not line or lowered in seen:
            continue
        if _contains_irrelevant_marker(line):
            break
        seen.add(lowered)
        result.append(line)
    return "\n".join(result) or None
