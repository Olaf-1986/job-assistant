# Job Assistant: Current Technical State

This is the single current technical-state handoff. See [README.md](README.md) for installation and commands and
[AGENTS.md](AGENTS.md) for repository rules, invariants, and required verification. Historical specifications are not
current scope.

## Pipeline Topology

`fetch-all` has one automatic vacancy-source producer: HeadHunter through the official vacancies API. Its sequence is:

1. run the HeadHunter source;
2. run enabled email ingestion as a separate, isolated stage;
3. rebuild combined outputs through the shared pipeline.

Email errors are sanitized and isolated so successful source data can continue through rebuild and export. Email is an
ingestion stage, not another automatic vacancy-source fetch.

Every raw vacancy passed to normalization has an explicit `__source` of `headhunter` or `linkedin`. The shared pipeline
then normalizes, deduplicates, filters, scores, and persists/exports records. Missing, unknown, or conflicting raw source
markers are errors; legacy persisted records receive their source only at their known persistence boundary.

## Ingestion Boundaries

### HeadHunter

- Searches use the official API, including configured search text, area, schedule, and work format.
- Remote searches have no timezone restriction; onsite/hybrid searches are limited to Tbilisi.
- Explicit Russia-only work-location restrictions are blocked; Russian citizenship alone is not.
- HeadHunter email candidates are retrieved by vacancy ID through the official API.

### Email

- IMAPS opens only the configured mailbox in read-only mode and fetches with `BODY.PEEK[]`.
- Messages are never deleted, moved, modified, or marked as read; credentials and full bodies are never logged.
- Only trusted LinkedIn `/jobs/view/<id>` and HeadHunter `/vacancy/<id>` URLs are accepted. Arbitrary links are not
  followed.
- LinkedIn URLs are queued without fetching LinkedIn. Normal reruns are idempotent by IMAP UID and Message-ID.
- Derived/local state consists of `email_candidates.json`, `linkedin_email_queue.md`, and `email_state.json`.

### LinkedIn

- Queue items are processed only by user-triggered current-page capture through the Chromium extension or explicitly
  invoked Playwright through `linkedin-fetch`.
- `fetch --source linkedin` only reports instructions. It starts neither workflow and makes no LinkedIn request.
- Neither `fetch` nor `fetch-all` starts Playwright or opens a browser.
- Playwright uses only its dedicated local persistent profile and manual login. Login, CAPTCHA, authwall, or account
  restriction pages stop processing and are never bypassed.
- Extension and Playwright captures use explicit vacancy title/company data and enter the shared pipeline.

## Authoritative Data and Rebuilds

Combined output is derived only from the latest successful HeadHunter raw store and persisted LinkedIn extension,
Playwright, or manual captures. An old `combined_jobs.json` is never an authoritative input.

HeadHunter refreshes preserve LinkedIn captures. Failed or skipped HeadHunter/email work does not erase successful
stored data. Repeated email ingestion, LinkedIn processing, vacancy IDs, and URLs remain idempotent.

## Scope Boundaries

Arbeitnow, Jooble, Jobicy, Greenhouse, and Lever are disabled optional/legacy code paths and never enter `fetch-all`.
Habr, Wellfound, Himalayas, Working Nomads, Built In, Dynamite Jobs, and Glassdoor are not active sources. Telegram is
planned but not implemented.
