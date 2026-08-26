# Changelog

Notable project changes are recorded here.

The project has no tagged release yet; current changes remain under Unreleased.

## Unreleased

### Added

- Added repository-wide agent instructions for safe, consistent development and verification.
- Added sanitized local LinkedIn rate-limit state with a two-minute start lock and no automatic retry or resume.
- Added Ruff configuration and pinned GitHub Actions checks for formatting, linting, tests, and configuration validation.
- Added a safe `.env.example` setup template for implemented workflows, with disabled legacy configuration clearly separated.
- Added uvicorn as the runtime dependency for the local capture server.

### Changed

- Consolidated active documentation into a user README, repository agent rules, and one current technical-state handoff; historical specifications remain explicitly archived.
- Added randomized 5–20 second LinkedIn page pacing, blocked image/media/font requests, and included HeadHunter detail tags in role analysis and scoring.
- Formalized LinkedIn queue processing through either user-triggered Chromium extension capture or explicitly invoked `linkedin-fetch` Playwright processing. `fetch --source linkedin` and `fetch-all` never start LinkedIn Playwright.
- Refactored shared pipeline modules to be source-neutral while preserving normalization, deduplication, filtering, scoring, persistence, and export behavior.
- Archived the historical first vertical-slice specification as non-authoritative reference material.

### Fixed

- Simplified shortlist Markdown while retaining Vacancy URLs and surfacing incomplete descriptions as deduplicated manual-review warnings.
- Recognized common English and Russian business- and systems-analyst title variants in HeadHunter prefiltering and role relevance.
- Removed unused citizenship and excluded-title settings, stale source search-count status, and an unused fetch-all helper argument.
- Removed unused export and language helpers, the unused pandas dependency, and ignored configuration fields.
- Required explicit raw-record source markers, migrated legacy HeadHunter and LinkedIn records at their persistence boundaries, and removed structural source fallback during normalization.
- Added the `job-assistant` console entry point, replaced placeholder package metadata, and removed the unused root `main.py` placeholder.
- Made `fetch-all` respect disabled email configuration and isolate sanitized IMAP, SSL, and network failures so the remaining pipeline can complete.
- Corrected LinkedIn queue counts to include every pending-compatible status while retaining compatibility with persisted `manual_capture_required` entries.
- Updated the capture endpoint test harness to use an in-process ASGI transport compatible with the current dependency stack.
- Protected exported CSV cell values from spreadsheet formula injection while preserving the existing CSV schema and outputs.
- Sanitized error details before console display and persistence, including HeadHunter response-body handling.
- Hardened the local capture server with one-time in-memory token loading and strict localhost Host validation.
- Aligned email credential precedence and source status with the required IMAP username and app password.
- Sanitized shortlist Markdown titles to prevent injected headings, links, formatting, HTML, and multiline structure.
- Hardened JSON persistence with atomic writes, corrupt-file recovery, and serialized capture updates.
- Refined LinkedIn page classification to ignore ordinary vacancy footer sign-in text while preserving login/authwall detection.
- Updated LinkedIn Playwright extraction to wait for substantive main content, use the queued title with document-title fallback, and retain the full vacancy description, including requirements sections, without brittle page selectors.
- Classified LinkedIn vacancies marked as no longer accepting applications as expired with an explicit blocker reason.

### Removed

- Removed deprecated Himalayas, Working Nomads, Built In, Dynamite Jobs, and Glassdoor sources from active code and configuration.
