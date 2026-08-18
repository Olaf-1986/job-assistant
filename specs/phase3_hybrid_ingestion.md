# Final Source Strategy

Active project scope is HeadHunter plus manual LinkedIn capture.

## Active Sources

- HeadHunter is the only automatic source and must use only the official vacancies API.
- Email job-alert ingestion uses read-only IMAPS against the configured Gmail mailbox/label.
- LinkedIn is manual current-page capture through the Chromium extension.
- `fetch-all` must run HeadHunter, then email ingestion before rebuilding outputs.
- `fetch --source linkedin` must return manual-capture instructions and must not make network requests.

## Removed Sources

Habr and Wellfound are not active sources. Do not keep Habr/Wellfound connectors, browser config, browser login, Playwright auth/profile logic, fixtures, tests, outputs, or current documentation.

Legacy disabled Arbeitnow, Jooble, Jobicy, Greenhouse, and Lever code may remain, but must never enter `fetch-all`. Himalayas, Working Nomads, Built In, Dynamite Jobs, and Glassdoor are deprecated legacy sources and have been removed from active code and configuration.

## Persistence

Output files are derived. Rebuild combined outputs only from latest successful HeadHunter raw data and LinkedIn manual captures. Do not trust old combined output as an input. HeadHunter refreshes must preserve LinkedIn captures. LinkedIn captures must not preserve stale removed-source, demo, or fixture records.

Email ingestion writes local derived/state files: `email_candidates.json`, `linkedin_email_queue.md`, and `email_state.json`. Normal reruns must be idempotent by IMAP UID and Message-ID.

## Email Rules

- Use standard-library `imaplib` over IMAPS.
- Host, port, and mailbox come from email environment settings with Gmail defaults.
- Credentials come only from `.env`.
- Open only the configured mailbox/label read-only.
- Fetch messages with `BODY.PEEK[]` only.
- Never delete, move, modify, or mark messages as read.
- Never log credentials or full email bodies.
- Accept only LinkedIn `/jobs/view/<id>` and HeadHunter `/vacancy/<id>` links.
- Never follow arbitrary links from email.
- LinkedIn links go to a manual-capture queue.
- HeadHunter IDs are retrieved through the official API and fed through the normal pipeline.

## HeadHunter Rules

- Remote searches have no timezone restrictions.
- Onsite/hybrid searches are accepted only for Tbilisi.
- Send configured official API parameters, including search text, host, area, schedule, and work_format.
- Block explicit Russia-only work-location restrictions.
- Do not block Russian citizenship by itself.
- Tests must use fixtures/mocks and must not make live HeadHunter requests.

## LinkedIn Rules

- Capture remains user-triggered only.
- Extension popup captures editable vacancy title and company fields.
- Normalization uses explicit captured title/company instead of treating the full browser document title as the vacancy title.
