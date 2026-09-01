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

Every raw vacancy passed to normalization has an explicit `__source` of `headhunter`, `linkedin`, or `telegram`. The shared pipeline
then normalizes, deduplicates, filters, scores, and persists/exports records. Missing, unknown, or conflicting raw source
markers are errors; legacy persisted records receive their source only at their known persistence boundary.

## Ingestion Boundaries

### HeadHunter

- Searches use the official API, including configured search text, area, schedule, and work format.
- Detail-response skills, professional roles, specializations, and available tag fields are retained as analysis tags
  for role filtering and scoring without modifying the vacancy description.
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
- Normal Playwright processing blocks image, media, and font resources and selects a non-repeating consecutive delay
  in the 5–20 second range after each opened vacancy.
- An explicit LinkedIn rate-limit signal stops immediately without retry, stores sanitized local timing state, and
  prevents another invocation from starting for two minutes. There is no automatic resume; processing continues only
  through a later, separate command.
- Vacancies explicitly marked as no longer accepting applications are expired and retain a blocker reason.
- Extension and Playwright captures use explicit vacancy title/company data and enter the shared pipeline.

### Telegram

- Telegram runs only through explicit `telegram-login`, `telegram-fetch`, and `telegram-audit` commands; it is never
  started by `fetch` or `fetch-all`.
- Telethon uses the user's own API credentials and a separate local session. Verification-code and optional 2FA input
  are hidden, and session/authentication artifacts are ignored by Git. Before the code prompt, login prints only the
  API-provided delivery type, next delivery type, and retry timeout; transport failures are classified separately
  from authorization and code errors. `telegram-login --qr` uses Telethon QR login and renders only a local terminal
  matrix with explicit black/white cells; the QR URL/token is never printed, saved, or sent to an external service.
  Its update waiter is active before the matrix is displayed. Each server-expiring token may be refreshed locally,
  for a total 60-second login window. Expiration and post-scan 2FA are handled without exposing secrets. POSIX session
  directories/files are restricted to `0700`/`0600`, including paths already ending in `.session`; insecure arbitrary
  existing parent directories are rejected rather than chmod'd.
- The adapter enumerates existing dialogs and reads only configured allowlisted channel/group usernames that the
  account has already joined. It never sends, edits, deletes, forwards, joins, or globally searches.
- Sources are processed sequentially with a configurable pause. Normal FloodWait values are honored and long waits
  stop safely without bypass behavior.
- Initial runs read three days. Later runs use per-source checkpoints plus a configurable recent-edit lookback.
- `--limit` is dry-run-only so a partial newest-first read cannot advance a checkpoint past unread history.
- `--all-history` with a dry-run limit globally selects the newest messages without a day cutoff, and
  `--show-targets` prints the eligible shared-pipeline preview in canonical score/date order without persistence.
- The opt-in `--export-shortlist` dry-run flag writes only that preview to `output/shortlist_tg.md`; it does not import,
  checkpoint, update source status, or rebuild combined outputs.
- The opt-in `--export-channel-check` dry-run flag reads the complete requested day window despite checkpoints and
  writes a timestamped, count-ranked per-channel report set plus `index.md`; shortlist entries expose the original
  Telegram channel and post URL.
- Telegram messages are parsed and scored deterministically and are never sent to an LLM, embedding service, or other
  AI/ML system. External application URLs are preserved but never crawled.
- Stable source identity is `channel_id:message_id:vacancy_index`; edits preserve the API's original publication date.
- `telegram-audit` reports current-window yield and pairwise containment, consulting the preceding equal window only
  for conservative removal-review evidence. Removal review is emitted only for consecutive three-day windows. It never
  modifies configured source lists.

## Authoritative Data and Rebuilds

Combined output is derived only from the latest successful HeadHunter raw store, persisted LinkedIn extension,
Playwright, or manual captures, and persisted Telegram raw records. An old `combined_jobs.json` is never an
authoritative input.

HeadHunter refreshes preserve LinkedIn and Telegram captures. Failed or skipped HeadHunter/email work does not erase
successful stored data. Repeated email ingestion, LinkedIn processing, Telegram message identities, vacancy IDs, and
URLs remain idempotent.

## Scope Boundaries

Arbeitnow, Jooble, Jobicy, Greenhouse, and Lever are disabled optional/legacy code paths and never enter `fetch-all`.
Habr, Wellfound, Himalayas, Working Nomads, Built In, Dynamite Jobs, and Glassdoor are not active sources.
