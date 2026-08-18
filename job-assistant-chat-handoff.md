# Job Assistant Handoff

## Current Source Strategy

- HeadHunter is the only automatic source.
- HeadHunter uses only the official vacancies API.
- Email job-alert ingestion is read-only IMAPS against the configured Gmail label/mailbox.
- LinkedIn is manual current-page capture through the Chromium extension.
- `fetch-all` runs HeadHunter, then email ingestion, then rebuilds outputs.
- `fetch --source linkedin` must return manual-capture instructions and must not fetch LinkedIn.

## Removed Scope

Habr and Wellfound are removed from the active project. Do not restore browser-source configuration, Playwright logic, browser login, browser profiles, auth state, fixtures, outputs, or current documentation for them.

Legacy disabled Arbeitnow, Jooble, Jobicy, Greenhouse, and Lever code may remain, but must never enter `fetch-all`. Himalayas, Working Nomads, Built In, Dynamite Jobs, and Glassdoor are deprecated legacy sources and have been removed from active code and configuration.

## Persistence Rules

Output files are derived data. Combined outputs must be rebuilt only from:

- latest successful HeadHunter raw data;
- LinkedIn manual captures/imports.

Email ingestion writes `email_candidates.json`, `linkedin_email_queue.md`, and `email_state.json`. It must use read-only IMAP with `BODY.PEEK[]`, never mutate messages, never log credentials or full bodies, and never fetch LinkedIn links. HeadHunter email candidates are retrieved through the official HH API.

Do not use old `combined_jobs.json` as an input. A HeadHunter refresh must preserve LinkedIn captures. A LinkedIn capture must not preserve stale removed-source, demo, or fixture records. Failed/skipped HeadHunter runs must not replace valid LinkedIn data with empty output.

## Verification

Run offline only unless the user explicitly approves a live HeadHunter run:

```bash
uv run python -m job_assistant validate-config
uv run python -m job_assistant sources
uv run pytest
uv run python -m job_assistant --help
```

Do not make live HeadHunter requests in tests. Do not automate LinkedIn.
