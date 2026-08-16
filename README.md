# Job Assistant

Local, single-user vacancy pipeline for BA/SA, systems analyst, integration analyst, and Atlassian administration roles.

## Source Strategy

- `headhunter`: the only automatic source, using the official HeadHunter vacancies API.
- `email`: read-only Gmail job-alert ingestion through IMAPS for trusted HeadHunter and LinkedIn vacancy links.
- `linkedin`: manual current-page capture through the Chromium extension only.
- `arbeitnow`, `jooble`, `himalayas`, `jobicy`, `greenhouse`, and `lever`: legacy disabled/optional code paths; they never run from `fetch-all`.

No Habr, Wellfound, Playwright, browser login, browser profile, or RSS/browser-source workflow is part of the active project.

## Commands

```bash
uv run python -m job_assistant validate-config
uv run python -m job_assistant sources
uv run python -m job_assistant fetch --source headhunter
uv run python -m job_assistant fetch --source linkedin
uv run python -m job_assistant email-sync --since-days 30
uv run python -m job_assistant fetch-all
uv run python -m job_assistant capture-server
uv run python -m job_assistant shortlist
uv run pytest
```

`fetch-all` fetches HeadHunter, then runs read-only email ingestion before rebuilding outputs. `fetch --source linkedin` prints the manual-capture instructions and does not fetch LinkedIn.

## Persistence

Output files under `output/` are derived data. Combined outputs are rebuilt only from:

- latest successful `output/headhunter_raw.json`;
- LinkedIn records stored in `output/manual_imports.json` by the extension or explicit manual import.

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

## Gmail Job Alerts

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

The IMAP client opens only the configured mailbox in read-only mode, uses `BODY.PEEK[]`, and never deletes, moves, modifies, or marks messages as read. Only trusted vacancy URL patterns are accepted: LinkedIn `/jobs/view/<id>` and HeadHunter `/vacancy/<id>` domains. LinkedIn links are queued for manual browser-extension capture; HeadHunter IDs are looked up through the official API.

## Quality Rules

- Unknown work mode is not treated as remote.
- Onsite/hybrid outside Tbilisi is blocked.
- Explicit Russia-only work-location restrictions are blocked.
- Russian citizenship by itself is not blocked.
- Explicit German requirements are blocked or held for manual review.
- Role relevance normally requires a target-title match.
- Description-only relevance requires at least two distinct strong BA/SA, modeling, integration/API, Atlassian administration, SQL/data, or technical documentation signal groups.
- Token-boundary matching prevents `intern` from matching `internal` and `AI` from matching ordinary words.
