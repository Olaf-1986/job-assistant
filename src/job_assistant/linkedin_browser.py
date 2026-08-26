from __future__ import annotations

import re
from typing import Any, Callable, NoReturn

from .linkedin_content import REQUIREMENTS_HEADING_MARKERS, VACANCY_SECTION_MARKERS, _description_from_main_text
from .linkedin_policy import STOP_REASONS, classify_linkedin_page, has_linkedin_no_longer_accepting_marker
from .linkedin_types import LinkedInFetchError, LinkedInPageContent, LinkedInStopRun, LinkedInVisibleBlock
from .utils import ROOT, normalize_space

PROFILE_DIR = ROOT / "data" / "browser_profiles" / "linkedin"
MIN_SEMANTIC_READY_TEXT_LENGTH = 80
MIN_READY_MAIN_TEXT_LENGTH = 500
FALLBACK_READY_MAIN_TEXT_LENGTH = 2_500
_JOB_CONTENT_SELECTOR = "main, [role='main'], article"
_VISIBLE_BLOCK_SELECTOR = "h1, h2, h3, h4, h5, h6, [role='heading'], p, li, strong, b, section, article, div"
_EXCLUDED_CONTENT_SELECTOR = (
    "nav, footer, aside, form, [role='navigation'], [role='complementary'], [role='contentinfo'], [aria-hidden='true']"
)
_SHOW_MORE_PATTERNS = (
    r"show\s+more",
    r"see\s+more",
    r"показать\s+(?:еще|ещё|больше)",
    r"mehr\s+anzeigen",
    r"voir\s+plus",
    r"ver\s+m[aá]s",
    r"mostrar\s+m[aá]s",
    r"mostra\s+altro",
)
_MAX_SHOW_MORE_CLICKS = 8
_BLOCKED_RESOURCE_TYPES = {"font", "image", "media"}


def _main_text_is_ready(main_text: str) -> bool:
    text = normalize_space(main_text)
    if len(text) < MIN_READY_MAIN_TEXT_LENGTH:
        return False
    lowered = text.casefold()
    return any(marker in lowered for marker in VACANCY_SECTION_MARKERS) or len(text) >= FALLBACK_READY_MAIN_TEXT_LENGTH


def _job_content_locator(page: Any) -> Any:
    candidates = page.locator(_JOB_CONTENT_SELECTOR)
    if not candidates.count():
        raise LinkedInFetchError("extraction_failed: semantic job-content container is missing")
    best_index = candidates.evaluate_all(
        """elements => {
            let bestIndex = -1;
            let bestLength = 0;
            for (const [index, element] of elements.entries()) {
                const style = window.getComputedStyle(element);
                if (style.display === 'none' || style.visibility === 'hidden' || !element.getClientRects().length) {
                    continue;
                }
                const text = (element.innerText || '').trim();
                if (text.length > bestLength) {
                    bestIndex = index;
                    bestLength = text.length;
                }
            }
            return bestIndex;
        }"""
    )
    if not isinstance(best_index, int) or best_index < 0:
        best_index = 0
    return candidates.nth(best_index)


def _visible_blocks_from_page(job_content: Any) -> tuple[LinkedInVisibleBlock, ...]:
    raw_blocks = job_content.evaluate(
        f"""root => {{
            const blockSelector = {_VISIBLE_BLOCK_SELECTOR!r};
            const excludedSelector = {_EXCLUDED_CONTENT_SELECTOR!r};
            const containerTags = new Set(['SECTION', 'ARTICLE', 'DIV']);

            const isVisible = element => {{
                const style = window.getComputedStyle(element);
                return style.display !== 'none'
                    && style.visibility !== 'hidden'
                    && !element.closest(excludedSelector)
                    && element.getAttribute('aria-hidden')?.toLowerCase() !== 'true'
                    && Boolean(element.getClientRects().length);
            }};
            const directText = element => Array.from(element.childNodes)
                .filter(node => node.nodeType === Node.TEXT_NODE)
                .map(node => node.textContent || '')
                .join(' ')
                .replace(/\\s+/g, ' ')
                .trim();

            return Array.from(root.querySelectorAll(blockSelector))
                .filter(element => isVisible(element))
                .map(element => {{
                    const tag = element.tagName.toLowerCase();
                    const role = element.getAttribute('role');
                    const isHeading = /^h[1-6]$/.test(tag) || role === 'heading';
                    const hasBlockChild = Array.from(element.children)
                        .some(child => child.matches(blockSelector));
                    let text = (element.innerText || '').trim();
                    if (containerTags.has(element.tagName) && hasBlockChild) {{
                        text = directText(element);
                    }}
                    if (!text) return null;
                    const rawLevel = element.getAttribute('aria-level');
                    const nativeLevel = /^h[1-6]$/.test(tag) ? Number(tag.slice(1)) : null;
                    const parsedLevel = rawLevel ? Number.parseInt(rawLevel, 10) : nativeLevel;
                    return {{
                        tag,
                        role,
                        ariaLevel: Number.isInteger(parsedLevel) ? parsedLevel : null,
                        text,
                        isHeading,
                    }};
                }})
                .filter(Boolean)
                .map((block) => {{
                    delete block.isHeading;
                    return block;
                }});
        }}"""
    )
    if not isinstance(raw_blocks, list):
        return ()
    blocks: list[LinkedInVisibleBlock] = []
    for raw_block in raw_blocks:
        if not isinstance(raw_block, dict):
            continue
        text = normalize_space(str(raw_block.get("text") or ""))
        if not text:
            continue
        role = normalize_space(str(raw_block.get("role") or "")) or None
        raw_level = raw_block.get("ariaLevel")
        aria_level = raw_level if isinstance(raw_level, int) else None
        blocks.append(
            LinkedInVisibleBlock(
                tag=normalize_space(str(raw_block.get("tag") or "div")).lower(),
                role=role,
                aria_level=aria_level,
                text=text,
            )
        )
    return tuple(blocks)


def _wait_for_linkedin_page(page: Any, *, require_substantive: bool = True) -> None:
    page.wait_for_function(
        """async ({ minimumLength, semanticMinimumLength, fallbackLength, markers,
            requireSubstantive, stabilityMs }) => {
            const candidates = Array.from(document.querySelectorAll("main, [role='main'], article"));
            const visibleCandidates = candidates.filter(element => {
                const style = window.getComputedStyle(element);
                return style.display !== 'none'
                    && style.visibility !== 'hidden'
                    && !element.closest("[aria-hidden='true']")
                    && Boolean(element.getClientRects().length);
            });
            if (!visibleCandidates.length) return false;
            const main = visibleCandidates.reduce((longest, current) => {
                const currentLength = (current.innerText || '').trim().length;
                const longestLength = (longest.innerText || '').trim().length;
                return currentLength > longestLength ? current : longest;
            });
            const readText = () => (main.innerText || '').trim();
            const before = readText();
            const minimum = requireSubstantive ? minimumLength : semanticMinimumLength;
            if (before.length < minimum) return false;
            if (requireSubstantive) {
                const lowered = before.toLowerCase();
                if (!markers.some(marker => lowered.includes(marker)) && before.length < fallbackLength) {
                    return false;
                }
            }
            await new Promise(resolve => setTimeout(resolve, stabilityMs));
            if (!document.documentElement.contains(main)) return false;
            const after = readText();
            if (after !== before) return false;
            if (!requireSubstantive) return true;
            const lowered = after.toLowerCase();
            return markers.some(marker => lowered.includes(marker)) || after.length >= fallbackLength;
        }""",
        arg={
            "minimumLength": MIN_READY_MAIN_TEXT_LENGTH,
            "semanticMinimumLength": MIN_SEMANTIC_READY_TEXT_LENGTH,
            "fallbackLength": FALLBACK_READY_MAIN_TEXT_LENGTH,
            "markers": REQUIREMENTS_HEADING_MARKERS,
            "requireSubstantive": require_substantive,
            "stabilityMs": 250,
        },
        timeout=15_000,
    )


def _wait_for_linkedin_page_or_stop(page: Any, *, require_substantive: bool = True) -> None:
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

    try:
        _wait_for_linkedin_page(page, require_substantive=require_substantive)
    except PlaywrightTimeoutError as exc:
        body_text = _read_linkedin_text(page.locator("body"), page.url, "body")
        reason = classify_linkedin_page(page.url, body_text)
        if reason in STOP_REASONS:
            signal = "page_marker" if reason == "rate_limited" else None
            raise LinkedInStopRun(reason, signal=signal) from exc
        if reason == "expired":
            try:
                job_text = _read_linkedin_text(_job_content_locator(page), page.url, "job content")
            except LinkedInFetchError:
                raise LinkedInFetchError("expired") from exc
            if has_linkedin_no_longer_accepting_marker(_description_from_main_text(job_text)):
                raise LinkedInFetchError("expired") from exc
        raise LinkedInFetchError("extraction_failed: page content did not become ready") from exc


def _read_linkedin_text(locator: Any, page_url: str, label: str) -> str:
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

    try:
        return locator.inner_text(timeout=5_000)
    except PlaywrightTimeoutError as exc:
        reason = classify_linkedin_page(page_url, "")
        if reason in STOP_REASONS:
            raise LinkedInStopRun(reason) from exc
        raise LinkedInFetchError(f"extraction_failed: {label} text did not become readable") from exc


def _raise_page_timeout(page: Any, message: str, exc: BaseException) -> NoReturn:
    reason = classify_linkedin_page(str(getattr(page, "url", "")), "")
    if reason in STOP_REASONS:
        raise LinkedInStopRun(reason) from exc
    raise LinkedInFetchError(f"extraction_failed: {message}") from exc


def _route_linkedin_resource(route: Any) -> None:
    if route.request.resource_type in _BLOCKED_RESOURCE_TYPES:
        route.abort()
        return
    route.continue_()


def _expand_show_more(page: Any) -> None:
    try:
        job_content = _job_content_locator(page)
    except (AttributeError, LinkedInFetchError):
        job_content = page

    clicks = 0
    for _ in range(2):
        clicked_this_round = False
        for pattern in _SHOW_MORE_PATTERNS:
            if clicks >= _MAX_SHOW_MORE_CLICKS:
                return
            try:
                buttons = job_content.get_by_role("button", name=re.compile(pattern, re.IGNORECASE))
                for index in range(min(buttons.count(), _MAX_SHOW_MORE_CLICKS - clicks)):
                    button = buttons.nth(index)
                    if hasattr(button, "is_visible") and not button.is_visible():
                        continue
                    if hasattr(button, "get_attribute") and button.get_attribute("aria-expanded") == "true":
                        continue
                    button.click(timeout=2_000)
                    clicks += 1
                    clicked_this_round = True
                    if clicks >= _MAX_SHOW_MORE_CLICKS:
                        return
            except Exception:
                continue
        if not clicked_this_round:
            return


def _playwright_page_fetcher(headless: bool = False) -> Callable[[str], LinkedInPageContent]:
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    playwright = sync_playwright().start()
    context = playwright.chromium.launch_persistent_context(str(PROFILE_DIR), headless=headless)
    context.route("**/*", _route_linkedin_resource)
    page = context.pages[0] if context.pages else context.new_page()

    class Fetcher:
        def __call__(self, url: str) -> LinkedInPageContent:
            try:
                response = page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            except PlaywrightTimeoutError as exc:
                _raise_page_timeout(page, "navigation did not complete", exc)
            if response is not None and response.status == 429:
                raise LinkedInStopRun("rate_limited", signal="http_429")
            _wait_for_linkedin_page_or_stop(page, require_substantive=False)
            _expand_show_more(page)
            _wait_for_linkedin_page_or_stop(page)
            body_text = _read_linkedin_text(page.locator("body"), page.url, "body")
            reason = classify_linkedin_page(page.url, body_text)
            if reason in STOP_REASONS:
                signal = "page_marker" if reason == "rate_limited" else None
                raise LinkedInStopRun(reason, signal=signal)
            try:
                job_content = _job_content_locator(page)
                main_text = _read_linkedin_text(job_content, page.url, "job content")
                visible_blocks = _visible_blocks_from_page(job_content)
                document_title = page.title()
            except PlaywrightTimeoutError as exc:
                _raise_page_timeout(page, "job content did not become readable", exc)
            if has_linkedin_no_longer_accepting_marker(_description_from_main_text(main_text)):
                raise LinkedInFetchError("expired")
            return LinkedInPageContent(page.url, body_text, main_text, document_title, visible_blocks)

        def close(self) -> None:
            context.close()
            playwright.stop()

    return Fetcher()


def run_login() -> None:
    from playwright.sync_api import sync_playwright

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(str(PROFILE_DIR), headless=False)
        page = context.pages[0] if context.pages else context.new_page()
        page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded")
        input("Log in to LinkedIn in the opened Chromium window, then press Enter here to close it.")
        context.close()
