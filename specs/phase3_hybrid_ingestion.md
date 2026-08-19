# Final Source Strategy

Active project scope is HeadHunter, read-only email ingestion, and explicit LinkedIn queue processing.

## Active Sources

- HeadHunter is the only vacancy source executed by `fetch-all` and must use only the official vacancies API.
- Email job-alert ingestion uses read-only IMAPS against the configured Gmail mailbox/label and runs from `fetch-all`.
- LinkedIn queue items support user-triggered current-page capture through the Chromium extension and explicitly invoked Playwright processing through `linkedin-fetch`.
- `fetch-all` must run HeadHunter, then email ingestion before rebuilding outputs.
- `fetch --source linkedin` must return actionable instructions for both LinkedIn workflows, start neither workflow, and make no LinkedIn network request.
- Playwright must never start implicitly through `fetch` or `fetch-all`; LinkedIn browser automation is opt-in and must not be scheduled or treated as an automatic source.

## Removed Sources

Habr and Wellfound are not active sources. Do not keep Habr/Wellfound connectors, browser config, browser login, Playwright auth/profile logic, fixtures, tests, outputs, or current documentation for those removed sources. This prohibition does not apply to the supported explicit LinkedIn workflow.

Legacy disabled Arbeitnow, Jooble, Jobicy, Greenhouse, and Lever code may remain, but must never enter `fetch-all`. Himalayas, Working Nomads, Built In, Dynamite Jobs, and Glassdoor are deprecated legacy sources and have been removed from active code and configuration.

## Persistence

Output files are derived. Rebuild combined outputs only from latest successful HeadHunter raw data and successful LinkedIn extension, Playwright, or manual captures. Do not trust old combined output as an input. HeadHunter refreshes must preserve LinkedIn captures. LinkedIn captures must not preserve stale removed-source, demo, or fixture records. Successful extension and Playwright captures use the same normalization, deduplication, filtering, scoring, persistence, and export pipeline.

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
- LinkedIn links enter the shared queue for either extension capture or explicit `linkedin-fetch` processing.
- HeadHunter IDs are retrieved through the official API and fed through the normal pipeline.

## HeadHunter Rules

- Remote searches have no timezone restrictions.
- Onsite/hybrid searches are accepted only for Tbilisi.
- Send configured official API parameters, including search text, host, area, schedule, and work_format.
- Block explicit Russia-only work-location restrictions.
- Do not block Russian citizenship by itself.
- Tests must use fixtures/mocks and must not make live HeadHunter requests.

## LinkedIn Rules

- Extension capture remains user-triggered current-page capture.
- Extension popup captures editable vacancy title and company fields.
- Normalization uses explicit captured title/company instead of treating the full browser document title as the vacancy title.
- Playwright queue processing runs only when `linkedin-fetch` is explicitly invoked; it never starts through `fetch` or `fetch-all`.
- Playwright uses only its dedicated local persistent profile, and login is manual.
- Login-required, CAPTCHA, account-restriction, and similar barrier pages stop processing and must never be bypassed.
- Successful extension and Playwright captures enter the same normalization, deduplication, filtering, scoring, persistence, and export pipeline.
