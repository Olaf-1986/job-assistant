# Changelog

Notable project changes are recorded here.

The project has no tagged release yet; current changes remain under Unreleased.

## Unreleased

### Added

- Added repository-wide agent instructions for safe, consistent development and verification.
- Added Ruff configuration and pinned GitHub Actions checks for formatting, linting, tests, and configuration validation.
- Added a safe `.env.example` setup template for implemented workflows, with disabled legacy configuration clearly separated.

### Changed

- Formalized LinkedIn queue processing through either user-triggered Chromium extension capture or explicitly invoked `linkedin-fetch` Playwright processing. `fetch --source linkedin` and `fetch-all` never start LinkedIn Playwright.
- Refactored shared pipeline modules to be source-neutral while preserving normalization, deduplication, filtering, scoring, persistence, and export behavior.
- Archived the historical first vertical-slice specification as non-authoritative reference material.

### Fixed

- Corrected LinkedIn queue counts to include every pending-compatible status while retaining compatibility with persisted `manual_capture_required` entries.
- Updated the capture endpoint test harness to use an in-process ASGI transport compatible with the current dependency stack.
- Protected exported CSV cell values from spreadsheet formula injection while preserving the existing CSV schema and outputs.

### Removed

- Removed deprecated Himalayas, Working Nomads, Built In, Dynamite Jobs, and Glassdoor sources from active code and configuration.
