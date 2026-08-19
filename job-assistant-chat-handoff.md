# Job Assistant Handoff

## Current Source Strategy

- HeadHunter is the only vacancy source executed by `fetch-all`.
- HeadHunter uses only the official vacancies API.
- Email job-alert ingestion is read-only IMAPS against the configured Gmail label/mailbox and runs from `fetch-all`.
- LinkedIn queue items support user-triggered current-page capture through the Chromium extension or explicitly invoked Playwright processing through `linkedin-fetch`.
- `fetch-all` runs HeadHunter, then email ingestion, then rebuilds outputs.
- `fetch --source linkedin` must return instructions for both LinkedIn workflows, start neither workflow, and make no LinkedIn request.
- Playwright must never start implicitly through `fetch` or `fetch-all`; LinkedIn browser automation is opt-in and is not a scheduled or automatic source.

## Removed Scope

Habr and Wellfound are removed from the active project. Do not restore browser-source configuration, Playwright logic, browser login, browser profiles, auth state, fixtures, outputs, or current documentation for those removed sources. This prohibition does not apply to the supported explicit LinkedIn workflow.

Legacy disabled Arbeitnow, Jooble, Jobicy, Greenhouse, and Lever code may remain, but must never enter `fetch-all`. Himalayas, Working Nomads, Built In, Dynamite Jobs, and Glassdoor are deprecated legacy sources and have been removed from active code and configuration.

## Persistence Rules

Output files are derived data. Combined outputs must be rebuilt only from:

- latest successful HeadHunter raw data;
- successful LinkedIn extension captures, explicit Playwright captures, and manual imports.

Email ingestion writes `email_candidates.json`, `linkedin_email_queue.md`, and `email_state.json`. It must use read-only IMAP with `BODY.PEEK[]`, never mutate messages, never log credentials or full bodies, and never fetch LinkedIn links. LinkedIn links enter the shared queue for extension capture or explicit `linkedin-fetch`; HeadHunter email candidates are retrieved through the official HH API.

Do not use old `combined_jobs.json` as an input. A HeadHunter refresh must preserve LinkedIn captures. A LinkedIn capture must not preserve stale removed-source, demo, or fixture records. Failed/skipped HeadHunter runs must not replace valid LinkedIn data with empty output.

## Verification

Run offline only unless the user explicitly approves a live HeadHunter run:

```bash
uv run python -m job_assistant validate-config
uv run python -m job_assistant sources
uv run pytest
uv run python -m job_assistant --help
```

Do not make live HeadHunter requests in tests. LinkedIn Playwright may run only through an explicitly invoked `linkedin-fetch`, uses only its dedicated local persistent profile, and requires manual login. CAPTCHA, account restrictions, login barriers, and similar obstacles must stop processing and must never be bypassed. Successful extension and Playwright captures must use the same normalization, deduplication, filtering, scoring, persistence, and export pipeline. Tests remain offline and must not use a live browser, network, credentials, `.env` values, or persistent profile.
