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

Every active raw vacancy passed to normalization has an explicit `__source` of `headhunter`, `linkedin`, `telegram`, or `rvc`. The shared pipeline
then normalizes, deduplicates, filters, scores, and persists/exports records. Missing, unknown, or conflicting raw source
markers are errors; legacy persisted records receive their source only at their known persistence boundary.
Description-language eligibility uses the shared `languages.accepted` allowlist (`en`, `ru`, `es`), validated to reject
other configured codes. The local deterministic detector does not default unknown text to English or all Cyrillic to
Russian. English headings, requirements excerpts, and manual language metadata cannot override the description.
Unsupported or unidentified descriptions are hard-blocked across all sources; filtering rechecks merged descriptions
after deduplication. LinkedIn's early language gate uses the same configured allowlist, including Spanish.
For full-page captures, language detection isolates the job-description section and excludes promotional UI/footer
text; header-only captures cannot qualify as English when the description is missing.
This conservative rule-based detection may reject ambiguous supported-language descriptions; no AI/ML or live service
is used for detection.
Explicit language requirements are an independent blocker: English descriptions requiring German fluency, for
example, remain excluded. Fluency/proficiency/native-language prefixes accept ordinary spaces, tabs, and line breaks;
both full descriptions and requirements sections are checked.
Different-URL reposts are deduplicated only when normalized company and title are exact and substantive description
word multisets overlap by at least 98%, in addition to the existing exact identity keys.
Known monthly salary maxima at or below `200,000 RUB` equivalent are blocked. With no maximum, known minima below
`150,000 RUB` equivalent are also blocked; USD and GEL use the configured rule-of-thumb conversion constants.
Bank of Georgia is permanently excluded. Reiz tech, Specific-Group, Keepgo, Dotmatics, Mad Brains, Selecty, Sibedge,
and Научсофт are excluded through 6 November 2026 inclusive; the existing Andersen exclusion lasts through 8 November.

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
- Vacancies explicitly marked as no longer accepting applications are persisted as expired, retain a blocker reason,
  and are excluded from rebuilt shortlists. `linkedin-fetch --recheck-shortlist` rereads already saved eligible
  candidates; `qf` processes every pending queue item in its requested freshness window without an additional item
  cap, then rechecks enough saved candidates for the requested LinkedIn shortlist size.
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

### RVC

- Explicit `rvc-discover` performs one anonymous MCP search with at most ten cards; no pagination, retry, browser,
  scheduler or automatic detail fan-out. It never runs from `fetch`, `fetch-all`, or `qf all`.
- `output/rvc_raw.json` is a versioned store of cards, first/last seen timestamps, run history and user reviews.
  Pending cards are not pipeline inputs. Rediscovery preserves reviewed text, publication dates and review status.
  Invalid stored data stops processing without moving or replacing the file.
- `rvc-review` takes a local JSON review with full text, original publication timestamp and affirmative location/open
  checks, or marks a candidate rejected/expired. Reviews are idempotent by RVC identity. Publication never falls back
  to discovery time. Country/relative-age metadata from MCP is insufficient to confirm eligibility or freshness.
- Reviewed records use the shared pipeline and source provenance; existing filters remain authoritative. Successful
  review persistence precedes export so a failed rebuild is retryable. Rejection/expiry preserves prior descriptions.
- `rvc-audit` measures current local overlap, incremental eligibility and review minutes. Its two-week assessment
  needs two distinct observation dates and completed reviews; three additional eligible records is the proposed
  threshold. No conclusion about global LinkedIn exclusivity is made.
- Provider questions about retention, scheduling and full-text access are drafted in `docs/rvc-access-questions.md`.
  They have not been sent or answered. Automated enrichment and periodic collection remain planned possibilities.

## Authoritative Data and Rebuilds

Combined output is derived only from the latest successful HeadHunter raw store, persisted LinkedIn extension,
Playwright, or manual captures, persisted Telegram raw records, and reviewed records in the RVC store.
An old `combined_jobs.json` is never an
authoritative input.

`combined_shortlist.md` is the canonical Markdown shortlist of the latest completed publication. `qf all` and
unscoped `shortlist` include all available saved sources; `qf hh|li|tg` and `shortlist --source` restrict it to the
selected source. Every shared export refreshes the source-attributed views
`shortlist_hh.md`, `shortlist_li.md`, `shortlist_tg.md`, and `shortlist_rvc.md`; the legacy generic `shortlist.md` is not written.
Available means valid authoritative stored data passing the shared pipeline, including earlier successful imports
when this run's source refresh failed, was disabled or was skipped. Missing stores are empty; damaged stores stop
rebuilding. Saved reviewed RVC entries can participate without running discovery. Saved fallback records may be stale.
The `qf` window scopes collection/rechecking and source-specific candidates/views; the all-source canonical candidate
pool is not date-trimmed. Undated records remain eligible.

`qf all` records independent HH, email, LinkedIn and Telegram stage results. A source error or LinkedIn protective
stop ends that stage without bypass/retry and allows other sources and final rebuilding from valid stores.
Console output and the final `summary.json` contain `success`/`skipped`/`error` stages and an overall `success` or
`partial` status. Any error yields exit 1 after publication; intentional skips alone yield 0. Source-specific `qf`
also publishes the selected saved fallback and exits 1 on a source failure. Ctrl+C/abort stops the whole command.
Authoritative-store damage and publication failures are fatal; they never become an apparently successful fallback
rebuild. Earlier source writes/publications can already have completed before a later stage aborts.
`shortlist-deduplicated` computes current candidates from authoritative stores, excludes identities found in the
combined-shortlist baseline saved by its previous invocation, writes `deduplicated_shortlist.md`, and then advances
the JSON baseline. That baseline is comparison state only and is never an authoritative vacancy input.

HeadHunter refreshes preserve LinkedIn, Telegram and reviewed RVC records. Failed or skipped HeadHunter/email work does not erase
successful stored data. Repeated email ingestion, LinkedIn processing, Telegram message identities, vacancy IDs, and
URLs remain idempotent.

HeadHunter completion is explicit and independent of result length. Both fetch commands replace the snapshot on a
complete success, including `[]`, and retain the old snapshot on errors, incomplete pagination/details, configured
page-cap truncation or skips. Pagination stops at its validated last page. HH success status follows publication.
Same-ID merges use `__hh_response` metadata stamped only on validated successful full API details (search/detail/email
origin and UTC receipt time). Newer proven responses replace older ones, independent of list order; failed/incomplete
responses do not replace successful records. Legacy records without metadata remain supported and yield to proven
full responses; unproven/tied freshness retains the existing record. Reimport remains idempotent by HH ID.
This uses the local clock, not HH publication dates, and leaves cross-source deduplication unchanged.

HH/manual/Telegram raw reads now validate JSON structure, source identity and core fields without moving or repairing
invalid files; RVC already used strict schema validation. Missing stores are valid first-run state. Legacy wrappers
and LinkedIn markers remain compatible. Even a new HH replacement does not bypass validation of the old store.
Derived-state `read_json` recovery policies remain separate.

The complete export bundle (including authoritative HH, JSON, CSV, all shortlist views, review/blocked Markdown and
summary) uses staged bytes, checksums and `.publication.json`. Recovery rolls the saved bundle forward before
authoritative reads, or through local `recover-output`; a damaged journal/staging set blocks recovery. Summary is
last. Deduplicated report/baseline, Telegram raw/checkpoints/failures and channel-check report sets use the same
mechanism. Sequential replacements are not an atomic set: readers can see intermediate files, the whole ingestion
workflow requires a single writer, source-first ingestion can precede report export, and power-loss durability is
filesystem-dependent. See README Persistence for recovery instructions and exact limits.

## Scope Boundaries

Arbeitnow, Jooble, Jobicy, Greenhouse, and Lever are disabled optional/legacy code paths and never enter `fetch-all`.
Habr, Wellfound, Himalayas, Working Nomads, Built In, Dynamite Jobs, and Glassdoor are not active sources.
