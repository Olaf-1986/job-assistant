# Job Assistant

Local, single-user vacancy pipeline for BA/SA, systems analyst, integration analyst, and Atlassian administration roles.

## Installation

The project requires Python 3.12 or newer and uses [uv](https://docs.astral.sh/uv/) for its environment:

```bash
uv sync
uv run job-assistant --help
```

Both supported CLI entry points invoke the same application and can be used interchangeably:

```bash
uv run job-assistant --help
uv run python -m job_assistant --help
```

Create a local `.env` from `.env.example` only when enabling a workflow that needs credentials. LinkedIn Playwright
processing additionally requires a local Chromium installation and remains an explicitly invoked workflow.

## Documentation

- This README is the user guide for setup, commands, and implemented behavior.
- [AGENTS.md](AGENTS.md) defines repository rules, invariants, and required checks for coding agents.
- [job-assistant-chat-handoff.md](job-assistant-chat-handoff.md) is the single current technical-state handoff.

Historical specifications under `specs/archive/` are non-authoritative and intentionally excluded from active
navigation.

## Source Strategy

- `headhunter`: the only vacancy source executed by `fetch-all`, using the official HeadHunter vacancies API.
- `email`: read-only Gmail job-alert ingestion through IMAPS for trusted HeadHunter and LinkedIn vacancy links; it runs from `fetch-all` but is not a vacancy-source fetch.
- `linkedin`: queued links can be processed either by manual current-page capture through the Chromium extension or by the explicit `linkedin-fetch` Playwright command.
- `arbeitnow`, `jooble`, `jobicy`, `greenhouse`, and `lever`: legacy disabled/optional code paths; they never run from `fetch-all`.

No Habr, Wellfound, or RSS workflow is part of the active project. Playwright is used only by the explicit LinkedIn queue workflow, with a dedicated local browser profile.

## Commands

```bash
uv run python -m job_assistant validate-config
uv run python -m job_assistant sources
uv run python -m job_assistant fetch --source headhunter
uv run python -m job_assistant fetch --source linkedin
uv run python -m job_assistant email-sync --since-days 30
uv run python -m job_assistant fetch-all
uv run python -m job_assistant capture-server
uv run python -m job_assistant linkedin-queue --status
uv run python -m job_assistant linkedin-fetch --limit 5 --dry-run
uv run python -m job_assistant shortlist
uv run pytest
```

`fetch-all` automatically fetches only HeadHunter. It then runs enabled read-only email ingestion as a separate stage
before rebuilding outputs. `fetch --source linkedin` starts neither LinkedIn workflow and makes no LinkedIn request; it
only prints instructions for extension capture and explicit `linkedin-fetch`. Playwright never starts implicitly
through `fetch` or `fetch-all`.

## Persistence

Output files under `output/` are derived data. Combined outputs are rebuilt only from:

- latest successful `output/headhunter_raw.json`;
- LinkedIn records stored in `output/manual_imports.json` by the extension, explicit `linkedin-fetch`, or explicit manual import.

Email ingestion also writes derived `output/email_candidates.json`, `output/linkedin_email_queue.md`, and `output/email_state.json`. These files are derived/local state and are safe to rebuild from the configured mailbox.

Rebuilds do not trust old `combined_jobs.json`. Failed or skipped HeadHunter runs do not replace valid LinkedIn captures with empty output.

## HeadHunter

HeadHunter uses configured official API search groups:

- remote search without timezone restrictions;
- onsite/hybrid search restricted to Tbilisi;
- strict title prefiltering before detail requests;
- optional OAuth through `HH_ACCESS_TOKEN` if `require_authentication: true` is configured.

The connector does not use Playwright, browser automation, CAPTCHA bypasses, or live requests in tests.

## LinkedIn Capture

Start the local receiver:

```bash
uv run python -m job_assistant capture-server
```

Install `browser_extension/` as an unpacked Chromium extension and configure the local token from `data/capture_token`. Open a LinkedIn job page, review/edit the prefilled vacancy title and company in the popup, then save. Capture is always user-triggered and sends visible page text to `http://127.0.0.1:8765/api/v1/manual-capture`.

Queued LinkedIn links can also be processed explicitly with Playwright:

```bash
uv run python -m job_assistant linkedin-fetch --login
uv run python -m job_assistant linkedin-fetch --limit 5 --dry-run
uv run python -m job_assistant linkedin-fetch --limit 5
```

`linkedin-fetch` is opt-in queue processing, not a scheduled or automatic source. `--login` opens a headed Chromium window for manual login and stores browser state only in the dedicated local persistent profile. A normal run applies the title prefilter before navigation, extracts JobPosting JSON-LD or supported DOM fields, and checkpoints each processed queue item. Login-required, CAPTCHA, account-restriction, or similar barrier pages stop the run and are never bypassed. Successful extension and Playwright captures enter the same normalization, deduplication, filtering, scoring, persistence, and export pipeline.

## Gmail Job Alerts

Create local environment configuration, then fill only the variables required for the workflows you enable:

```bash
cp .env.example .env
```

Configure `.env` with an app password for a dedicated Gmail label/mailbox:

```dotenv
EMAIL_IMAP_HOST=imap.gmail.com
EMAIL_IMAP_PORT=993
EMAIL_IMAP_USER=you@gmail.com
EMAIL_IMAP_APP_PASSWORD=your-app-password
EMAIL_IMAP_MAILBOX=JobAlerts
```

Run:

```bash
uv run python -m job_assistant email-sync --since-days 30
```

The IMAP client opens only the configured mailbox in read-only mode, uses `BODY.PEEK[]`, and never deletes, moves, modifies, or marks messages as read. Only trusted vacancy URL patterns are accepted: LinkedIn `/jobs/view/<id>` and HeadHunter `/vacancy/<id>` domains. LinkedIn links enter the shared queue for either user-triggered browser-extension capture or explicit `linkedin-fetch` processing; HeadHunter IDs are looked up through the official API.

## Quality Rules

- Unknown work mode is not treated as remote.
- Onsite/hybrid outside Tbilisi is blocked.
- Explicit Russia-only work-location restrictions are blocked.
- Russian citizenship by itself is not blocked.
- Explicit German requirements are blocked or held for manual review.
- Role relevance normally requires a target-title match.
- Description-only relevance requires at least two distinct strong BA/SA, modeling, integration/API, Atlassian administration, SQL/data, or technical documentation signal groups.
- Token-boundary matching prevents `intern` from matching `internal` and `AI` from matching ordinary words.
