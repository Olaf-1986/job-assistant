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

Create a local `.env` from `.env.example` only when enabling a workflow that needs credentials. Telegram uses
Telethon `>=1.44.0,<2`; LinkedIn Playwright processing additionally requires a local Chromium installation. Both
remain explicitly invoked workflows.

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
- `telegram`: explicit, read-only Telethon ingestion from configured channels/groups already joined by the account; it never runs from `fetch-all`.
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
uv run python -m job_assistant fetch-all --last day
uv run python -m job_assistant fetch-all --last 3-days
uv run python -m job_assistant fetch-all --last week
uv run python -m job_assistant fetch-all --last 2-weeks
uv run python -m job_assistant capture-server
uv run python -m job_assistant linkedin-queue --status
uv run python -m job_assistant linkedin-fetch --limit 5 --dry-run
uv run python -m job_assistant telegram-login
uv run python -m job_assistant telegram-login --qr
uv run python -m job_assistant telegram-fetch --since-days 3 --dry-run
uv run python -m job_assistant telegram-fetch --since-days 3
uv run python -m job_assistant telegram-fetch --limit 5 --dry-run
uv run python -m job_assistant telegram-fetch --limit 1000 --all-history --show-targets --export-shortlist --dry-run
uv run python -m job_assistant telegram-fetch --since-days 30 --export-shortlist --export-channel-check --dry-run
uv run python -m job_assistant telegram-audit --since-days 3
uv run python -m job_assistant shortlist
uv run python -m job_assistant shortlist --source headhunter
uv run python -m job_assistant shortlist --source linkedin
uv run python -m job_assistant shortlist --source telegram
uv run python -m job_assistant shortlist-deduplicated
uv run pytest
```

### Quick fetch aliases

Use the installed `qf WINDOW SOURCE SIZE` executable for a compact, composable fetch command. It is available directly
as `qf` while the project environment is active, or through `uv run qf` without activation:

```bash
qf 1d all 25
qf 2d hh 50
qf 3d li 20
qf 1w tg 30
qf 2w all 50
qf 3w hh 100
qf 1m all 50
```

Windows are `1d`, `2d`, `3d`, `1w`, `2w`, `3w`, and `1m`; sources are `all`, `hh`, `li`, and `tg`.
`SIZE` temporarily controls the generated shortlist length without editing `preferences.yaml`. `all` explicitly runs the
normal HH/email workflow followed by LinkedIn queue processing and read-only Telegram ingestion. LinkedIn is
queue-driven: `qf` processes every pending item inside `WINDOW`, using `published_at` or the email queue's
`received_at` fallback; there is no separate `qf` LinkedIn limit. Records with neither date remain eligible. Before
export, `qf` opens the current LinkedIn
shortlist candidates (and enough replacements for any closed entries), checks the vacancy content for an explicit
`No longer accepting applications` marker, and persists closed status as a blocker. The same check is available with
`linkedin-fetch --recheck-shortlist`; optionally pair it with `--since-days DAYS`. Normal LinkedIn page delays
and stop conditions still apply. Telegram uses the window on its initial read, while existing checkpoints still take
precedence. Add `--force` only when another successful HH fetch is intentional.
`output/combined_shortlist.md` is the canonical shortlist for the most recent completed publication: `qf ... all ...` includes all available sources,
while `qf ... li ...` (and other source-specific aliases) limits it to the selected source and publication window.
`shortlist --source hh|li|tg|rvc` has the same source scope; `shortlist` without `--source` uses all saved sources.
Every shared export also refreshes `output/shortlist_hh.md`, `output/shortlist_li.md`, `output/shortlist_tg.md`
and `output/shortlist_rvc.md` as views of their respective sources. The old `output/shortlist.md` is no longer part
of the pipeline. Fetch/import commands by themselves rebuild the combined view; the final step of a source-specific
`qf` sets its requested scope. In `all` mode, `WINDOW` bounds collection/rechecking, not the age of the saved
all-source canonical candidates. Source views apply the supplied publication window; undated records remain eligible.

Shortlist cards combine score, work format, language, salary and publication date on one line, and scoring reasons
and matched signals on another. Role relevance and employment type remain in the structured exports but are omitted
from shortlist cards. Location restrictions and Telegram references appear when present. Identical warning sets
across multiple displayed cards are omitted from Markdown; different warnings stay on their cards. All warnings
remain in structured exports.
For rendered Markdown in VS Code, open a shortlist and press `Ctrl+Shift+V`, or `Ctrl+K` then `V` for a side preview
([VS Code Markdown documentation](https://code.visualstudio.com/docs/languages/markdown)). Plain-text reading in
Notepad++ also works; vacancy URLs remain visible.

An available source has a valid authoritative store with eligible records, whether saved by this run or an earlier
one. A failed, disabled or skipped refresh does not exclude its saved records from `all`. Missing stores contribute
no records; damaged stores stop rebuilding. Reviewed saved RVC records can participate, but `qf` never starts RVC
discovery. Fallback records have not necessarily been rechecked for freshness or continued availability.

`qf all` runs HH/email, LinkedIn and Telegram independently. Source errors and LinkedIn protective stops are
reported with their reasons; a LinkedIn stop ends that stage immediately, without retries or bypasses. Other
independent stages continue, then the final shortlist is built from valid saved stores. The console and final
`summary.json` record each stage as `success`, `skipped` or `error`, with overall `status: partial` and exit code 1
if any stage failed. With no errors the exit code is 0, including intentional skips. Source-specific `qf` uses the
same rule and can publish its saved fallback while returning 1. Ctrl+C or an explicit abort stops the whole command;
storage/publication failures also stop it before any unsafe final rebuild. Earlier completed source writes remain
saved. A final publication failure retains the publication journal described below and never reports partial success.

The same temporary shortlist override is also available as `fetch -n SIZE`, `fetch-all -n SIZE`,
`linkedin-fetch -n SIZE`, `telegram-fetch -n SIZE`, and `shortlist -n SIZE`.

### Previously published vacancies

A final delivery consists of the ranked cards in `output/combined_shortlist.md` after a standalone shared export
(including a fetch or import that rebuilds it), or in `output/deduplicated_shortlist.md` after an explicit
`shortlist-deduplicated`. Each publication adds those cards to the same cumulative `output/shortlist_history.json`.
A source-specific run records only its canonical selection. Automatic source views, channel checks, previews, and
dry runs contribute zero deliveries. During `qf` and per-item LinkedIn imports, intermediate rebuilds can save
raw records and refresh derived views. The final publication writes the canonical report, history, and summary
together. A completed partial `qf` counts as a delivery even when the command exits 1.

Ranking skips matches from every previous delivery before applying the size limit, so lower-ranked fresh candidates
can fill the shortlist. An empty report preserves history. These commands allow repeats for one publication.

```bash
uv run qf 1w all 20 --include-seen
uv run python -m job_assistant shortlist --source li -n 20 --include-seen
uv run python -m job_assistant shortlist-deduplicated -n 20 --include-seen
```

The option keeps ordinary eligibility checks, including closure and confirmed-application blockers. New entries
in that run also enter history. The shared identity rules below apply across sources and to reposts. Raw and
combined vacancy stores retain seen records. The structured `previously_exported` field describes the comparison
at selection time.

On first publication the exporter seeds the history from ranked vacancy links in available canonical, deduplicated,
and legacy shortlist snapshots, including saved repair copies. It also retains identities from
`recovered_shortlist_state.json` together with the original evidence and exclusions. A matching historical
card can acquire aliases from the legacy `previous_combined_shortlist.json`. That baseline alone cannot establish
delivery. Source views, preview files and `combined_jobs.json` supply no delivery evidence. Older deliveries whose
snapshots and identities are missing remain unknown. Restore a damaged history before retrying publication.

`fetch-all` automatically fetches only HeadHunter. It then runs enabled read-only email ingestion as a separate stage
before rebuilding outputs. `fetch --source linkedin` starts neither LinkedIn workflow and makes no LinkedIn request; it
only prints instructions for extension capture and explicit `linkedin-fetch`. Playwright never starts implicitly
through `fetch` or `fetch-all`.

## Persistence

Combined outputs under `output/` are derived data. They are rebuilt only from these authoritative stores:

- latest successful `output/headhunter_raw.json`;
- LinkedIn records stored in `output/manual_imports.json` by the extension, explicit `linkedin-fetch`, or explicit manual import.
- Telegram records stored in `output/telegram_raw.json` by explicit `telegram-fetch` runs.
- Reviewed full-description records in `output/rvc_raw.json`; pending RVC cards are excluded.

Shared deduplication uses canonical URLs and source IDs, exact normalized descriptions, and company/title/location
identity. The company/title/location key is sufficient on its own, even when URLs, IDs, or descriptions differ;
the user accepts this tradeoff for otherwise indistinguishable titles. Text inside parentheses is part of the title.
Another rule matches different-URL reposts when normalized company and title are exact and substantive descriptions
have at least 98% word-multiset overlap.

Email ingestion also writes derived `output/email_candidates.json`, `output/linkedin_email_queue.md`, and `output/email_state.json`. These files are derived/local state and are safe to rebuild from the configured mailbox.

Telegram checkpoints and deterministic extraction failures are stored separately in `output/telegram_checkpoints.json`
and `output/telegram_failures.json`. Rebuilds do not trust old `combined_jobs.json`. Failed or skipped HeadHunter runs
do not replace valid LinkedIn, Telegram or reviewed RVC records with empty output.

Both `fetch` and `fetch-all` replace the HeadHunter snapshot only after a **complete successful search**, including
an empty result (`[]`). A failed, incomplete or skipped search preserves the preceding snapshot. Completeness requires
consistent pagination and all requested pages and relevant vacancy details. Reaching `pages_per_query` before the
last page, a truncated API result, malformed records, a page failure or a detail failure makes the whole search
incomplete. The configured page cap is never silently expanded. Narrow the queries/window or adjust the cap when
necessary. Successfully retrieved email HH records are added to the new complete snapshot or to the preserved one.
The daily successful-run status is recorded only after the resulting outputs have been published.

Same-ID HH updates use explicit successful-response metadata (`__hh_response`: origin, completeness and UTC
receipt time) recorded only after validating a full API detail response. The newer successful observation wins
in search/email merges regardless of list order; errors, incomplete details or unverified observations cannot
replace a proven successful record. A verified full response can replace a legacy record without metadata.
Legacy records remain readable; when neither observation proves freshness (or receipt times tie), the existing
record is retained. Repeated imports keep one record per HH ID. These rules do not change cross-source deduplication
or complete-snapshot replacement, including a successful empty snapshot. Receipt-time ordering assumes a reliable
local clock; HH publication dates are not used as update timestamps.

Authoritative HH, manual/LinkedIn, Telegram and RVC stores are read strictly. Missing files are allowed on the first
run; existing malformed JSON, invalid record containers, incompatible source markers or invalid core fields stop
processing with a content-free error. The original file stays in place, including when a replacement HH result is
available. Existing wrapped/unwrapped records and legacy LinkedIn source markers remain supported. An HH record
without a title is rejected at the read boundary instead of being silently dropped by normalization. Repair invalid
data before retrying.
The older `read_json` quarantine/fallback behavior remains limited to its other callers, such as derived queue/status
and checkpoint state; it is not used to recover these authoritative stores.

`export_all` prepares a complete publication before replacing any destination: HH raw, normalized/combined JSON,
both CSV files, canonical and source-specific shortlists, blocked/role-review Markdown and summary. Every source
view is refreshed, including empty ones. The publication keeps prepared bytes and checksums under
`output/.publication-stage-*` and writes `output/.publication.json` before any replacement. Summary is replaced last.
A write/replace failure or process interruption after that point leaves the journal, so mixed results are detectable.
The next authoritative read/rebuild finishes **the same prepared set** before proceeding. Recovery validates every
staged file first, then repeats the replacements; it is safe to retry after another interruption. It never rebuilds
HH from a possibly mixed combined output. Recovery can also be invoked without any vacancy collection:

```bash
uv run python -m job_assistant recover-output
```

Do not use reports while `.publication.json` exists, or delete the journal or its referenced staging directory.
If either is damaged or missing data, automatic recovery stops without further replacements; restore the complete
staging set from a backup before retrying. Interrupted preparation may leave unreferenced staging directories, but
does not change the published files. A leftover `.publication.lock` file is normal: the operating-system lock is
released on process exit. Both final shortlist reports use the same publication protocol with their cumulative history.
Telegram raw/failure/checkpoint updates and each channel-check report set also publish together. Standalone Markdown
exports use a flushed temporary file and replacement instead of truncating the destination.

These sequential replacements are **not an atomic multi-file transaction**. External readers can see intermediate
files during publication and must coordinate with writers; the journal makes interruption visible but does not give
readers a snapshot. Run one ingestion/rebuild command at a time per output directory: the publication lock protects
publication/recovery, not the entire read/modify/write workflow. Source-first LinkedIn/Telegram/RVC ingestion and
email/status state remain separate from the combined report publication; if ingestion stops before export, rerun
`shortlist` to bring reports up to date with saved sources. Prepared files are flushed; directory syncing is used
where supported. Windows/WSL file replacement is supported without renaming open temporary files, but power-loss
durability depends on the filesystem and platform (portable directory syncing is unavailable on Windows). The
journal is recovery state, not a substitute for backups.

## HeadHunter

Use `--last day`, `--last 3-days`, `--last week`, or `--last 2-weeks` with `fetch` or `fetch-all` for a quick
publication-date window. HeadHunter receives the corresponding official API `period` value; on `fetch-all`, the email
alert lookback uses the same window. Omitting `--last` preserves the configured HH behavior and the existing 30-day
email lookback. LinkedIn remains queue-driven, while Telegram keeps its explicit `--since-days` option.

Bank of Georgia vacancies are permanently blocked because its roles require fluent Georgian. Andersen remains
temporarily blocked through 8 November 2026 inclusive. Reiz tech, Specific-Group, Keepgo, Dotmatics, Mad Brains,
Selecty, Sibedge, and Научсофт are temporarily blocked through 6 November 2026 inclusive; those exclusions expire
automatically on 7 November 2026.

HeadHunter uses configured official API search groups:

- remote search without timezone restrictions;
- onsite/hybrid search restricted to Tbilisi;
- strict title prefiltering before detail requests;
- optional OAuth through `HH_ACCESS_TOKEN` if `require_authentication: true` is configured.

The connector does not use Playwright, browser automation, CAPTCHA bypasses, or live requests in tests.

## RVC discovery pilot

RVC is an explicitly invoked discovery source using its [public MCP](https://app.rvc.global/for-ai).
Each invocation makes one search and returns at most ten teaser cards. RVC never runs from `fetch`, `fetch-all`,
or `qf all`. It requires no account, API key or browser. Optional periodic collection and automatic full-description
access remain subject to the [open questions for RVC](docs/rvc-access-questions.md); the draft has not been sent.

```bash
uv run python -m job_assistant rvc-discover --query "Business Analyst" --country GE --since-days 14 --limit 10
uv run python -m job_assistant rvc-queue
uv run python -m job_assistant rvc-audit
```

Use `--worldwide` for worldwide remote or `--dry-run` for a live search with no local writes. Configured accepted
languages are passed to RVC. A country search includes every work arrangement; onsite/hybrid eligibility still
requires the existing Tbilisi check during review. `since-days` is only an RVC discovery filter: its relative age
and cutoff may reflect ingestion, and its inferred country labels can disagree with the vacancy description.
A truncated response is reported without walking further result pages. Errors, access barriers and rate limits
stop the invocation without retries or modifying successful stored candidates.

Saved candidates live in `output/rvc_raw.json`. This versioned store contains discovery history, review status and
user-provided descriptions. Pending cards never enter combined outputs or shortlists. Repeated discovery preserves
their first-seen date and every existing review, including rejected/expired status. A malformed RVC store stops
processing and is retained unchanged. It is not silently repaired or replaced.

For an actual candidate, copy [the review example](docs/rvc-review.example.json) to an ignored file under
`input/manual/` and replace every example value with verified vacancy information. Provide the full description,
original publication timestamp with timezone, actual employer, work arrangement and location; confirm that the
position is open and permits work from Georgia. An employer URL is optional and is never fetched. Missing full
text, missing confirmations and a future publication date are rejected. Discovery time never substitutes for the
publication date. The confirmations attest to the user's review; the software cannot verify them independently.

```bash
uv run python -m job_assistant rvc-review MASKED_ID --file input/manual/rvc-review.json --minutes 5
uv run python -m job_assistant rvc-review MASKED_ID --reject --minutes 1
uv run python -m job_assistant rvc-review MASKED_ID --expired --minutes 1
uv run python -m job_assistant shortlist --source rvc --since-days 14 -n 5
```

Choose exactly one review action. `--minutes` is the total effort for this candidate, so repeating the command
does not double-count it; omitting it preserves the previous value. Reviewed descriptions enter the shared
normalize, deduplicate, filter, score and export
pipeline; acceptance into the review store does not override any blocker. Rejected/expired reviews retain prior
descriptions and block the record on subsequent rebuilds. Reviews are saved before derived exports; if export fails,
rerun after fixing the local error. HeadHunter refreshes preserve the RVC store. The RVC view is `shortlist_rvc.md`.

`rvc-audit` is local and read-only. It reports pending/rejected/expired candidates, reviewed eligibility, overlap
with saved LinkedIn records, additional eligible records absent from other current stores, truncation and review
minutes. These are local coverage metrics, not estimates of the whole LinkedIn catalog. The first successful saved
search starts the two-week observation period; dry runs do not start it. Evaluation requires at least two distinct
search dates and completed reviews. The proposed continuation threshold is three additional eligible vacancies.
The audit advises only and never changes source configuration or schedules another search.

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

`linkedin-fetch` is opt-in queue processing, not a scheduled or automatic source. `--login` opens a headed Chromium window for manual login and stores browser state only in the dedicated local persistent profile. A normal run applies the title prefilter before navigation, uses a newly selected 5–20 second delay after each opened vacancy, blocks image/media/font requests, extracts semantic job content, and checkpoints each processed queue item. `--recheck-shortlist` additionally rereads already saved, currently eligible LinkedIn candidates; `qf` enables that mode automatically. Login-required, CAPTCHA, account-restriction, or similar barrier pages stop the run and are never bypassed. An explicit rate-limit signal also stops immediately without retry, writes only sanitized local block timing to `output/linkedin_rate_limit_state.json`, and requires a separate invocation after two minutes. Successful extension and Playwright captures enter the same normalization, deduplication, filtering, scoring, persistence, and export pipeline. LinkedIn pages marked as no longer accepting applications are persisted as expired with a blocker reason and excluded from rebuilt shortlists.

## Telegram Ingestion

Use a separate Telegram account and create API application credentials locally at `my.telegram.org`. Copy
`.env.example` to `.env` and populate these values only on the local machine:

```dotenv
TELEGRAM_API_ID=
TELEGRAM_API_HASH=
TELEGRAM_PHONE=
TELEGRAM_SESSION_PATH=data/telegram_sessions/job_assistant
```

Do not paste credentials, verification codes, 2FA passwords, or session data into chat, logs, fixtures, or committed
files. `telegram-login` prompts for the normal verification code and optional 2FA password with hidden terminal input.
The resulting `.session*` files live under the ignored session directory and are restricted to the local user where
the platform permits. On POSIX systems, a newly created session directory is mode `0700` and session database artifacts
are mode `0600`. If `TELEGRAM_SESSION_PATH` uses an existing directory, restrict that directory to the local user first;
the command stops instead of changing permissions on an arbitrary existing parent directory. Before prompting, it prints
only Telegram's non-sensitive delivery metadata: the current
delivery type, next delivery type, and retry timeout. If the current type is `app`, check an already logged-in
Telegram client; Telegram may deliver the code there instead of by SMS. Connection failures are reported separately
from invalid credentials or verification-code errors.

Use `telegram-login --qr` to authorize by scanning a QR code from an already logged-in Telegram client. On an Android
or iPhone Telegram app, open `Settings` → `Devices` → `Link Desktop Device`, then point that built-in scanner at the
terminal. A regular phone is sufficient if it is already logged in to the same account; do not use the ordinary camera
or a third-party QR scanner. The QR matrix is generated locally in the terminal with the pure-Python `qrcode` package;
explicit black/white terminal cells keep it scannable on both light and dark terminal themes. No QR URL/token is printed,
saved, or sent to an external service. Telegram controls each token's individual expiry, so
the command refreshes an expired token locally and keeps the QR login window open for up to 60 seconds total. After
that window it stops safely and asks you to rerun the command. If the account requires 2FA, the password is requested
with hidden terminal input after the QR scan.

```bash
uv run python -m job_assistant telegram-login
uv run python -m job_assistant telegram-login --qr
uv run python -m job_assistant telegram-fetch --limit 5 --dry-run
uv run python -m job_assistant telegram-fetch --limit 1000 --all-history --show-targets --export-shortlist --dry-run
uv run python -m job_assistant telegram-fetch --since-days 3
```

The adapter is read-only: it never sends, edits, deletes, forwards, joins, or globally searches. It first enumerates
the account's existing dialogs, resolves only configured usernames found there, then reads each source sequentially
with the configured pause. Missing/unjoined sources are reported and never joined automatically. Telegram flood waits
up to the configured safe limit are honored; longer waits stop the run without a bypass.

The sole configured Telegram source is `careerspace`, based on the latest 30-day trial. All other evaluated sources
were removed from the active allowlist and are not read by future Telegram runs. `tbilisi_work` remains unconfigured
while membership approval is pending.
The zero-yield sources from the 30-day check were removed from the active allowlist; they are not read by future
Telegram runs. The excluded list is retained in configuration but never read:

- `habrcareer_bot`: duplicates Habr Career by origin;
- `g_jobbot`: interactive personalized bot requiring a separate adapter;
- `myjobit`: mixes office vacancies and candidate resumes with weak geographic precision;
- `twinslashjobs`: predominantly a low-volume single-employer feed.

The first fetch reads three days by default. Later runs start from each source checkpoint with a 12-hour configurable
lookback so recent edits are revisited. Identity is `channel_id:message_id:vacancy_index`; replacing an edited record
never changes its original `published_at`. A dry run reads and parses but does not import, checkpoint, update source
status, rebuild combined outputs, or write an audit. The explicit `--export-shortlist` option may write only the
eligible preview report to `output/shortlist_tg.md`; its vacancy URLs remain visible as copyable text. `--limit` is deliberately dry-run-only: persisting a newest-first partial
history could otherwise advance a checkpoint past unread older messages. Add `--all-history` to ignore the day cutoff:
the adapter inspects up to the limit from each joined source, globally selects the newest messages up to that limit,
then parses only that selection. `--show-targets` prints eligible vacancies after the shared normalization,
deduplication, filtering, and scoring pipeline in the same score/publication-date order as the canonical shortlist.
The parser never crawls application links.

`telegram-fetch --since-days` accepts a deterministic read window of 1–90 days. A one-off dry-run recheck can
temporarily add a previously removed source to the local allowlist, then restore the configured active sources.

`--export-channel-check` ignores saved checkpoints only for that dry run so it can inspect the complete requested
`--since-days` window. It creates a timestamped directory under `output/`, one Markdown shortlist for every configured
channel (including zero-result or unavailable channels), and a count-sorted `index.md`. Filenames are prefixed with
rank and passed-vacancy count. These reports contain only vacancies that pass the same deterministic normalization,
deduplication, filtering, and scoring path. Telegram-derived shortlist entries include the original `@channel` and
Telegram post URL alongside the copyable application URL.

Vacancy detection, multi-vacancy splitting, title prefiltering, and extraction are deterministic. Candidate resumes,
news, courses, advertising, events, and under-specified link-only posts are rejected or recorded as extraction
failures. Telegram content is never sent to an LLM, embeddings service, or other AI/ML system. Accepted records enter
the existing deterministic normalize, deduplicate, filter, score, and export path used by HeadHunter and LinkedIn.
The source-specific command writes `output/shortlist_tg.md` using the same eligibility and score/date ordering
as the combined shortlist:

```bash
uv run python -m job_assistant shortlist --source telegram
```

`telegram-audit --since-days 3` reports the current three-day window and reads the preceding equal window only to test
the conservative two-window removal rule. It reports per-source yield, parse failures, unique relevant vacancies,
and ordered pairwise containment:

```text
containment(X, Y) = vacancies from X also found in Y / valid vacancies from X
```

It only recommends review when both consecutive windows meet at least 80% containment, the containing source is no
later, and its relevant yield is at least as good. Removal review is emitted only for the specified consecutive
three-day windows; other `--since-days` values remain descriptive audits. It never changes source configuration. The report explicitly states
whether `it_job_offers` was active in the current window if that source is configured; otherwise it reports that the
source is not configured.

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
- Remote roles with unconfirmed eligibility from Georgia remain in the shortlist with a warning. Broad labels such
  as Europe/EMEA are not proof of a legal restriction; explicit restrictions still block. RVC records still require
  user review and confirmation of eligibility from Georgia before entering the pipeline.
- Onsite/hybrid outside Tbilisi is blocked.
- Explicit Russia-only work-location restrictions are blocked.
- Russian citizenship by itself is not blocked.
- Explicit requirements for languages other than Russian, English, or Spanish are blocked.
  This includes fluency requirements such as “Fluent in German, both written and spoken,” even when the
  description itself is English. Requirements in either the full description or its requirements section are checked.
- Mandatory advanced Spanish (C1/C2, fluent or native) is blocked; optional proficiency and alternatives satisfied
  by English are not mandatory Spanish requirements. The accepted description languages remain `en`, `ru`, `es`.
- Vacancy descriptions must be identified as Russian, English, or Spanish (`languages.accepted: [en, ru, es]`).
  This is a hard filter for every source, reapplied after deduplication. Unsupported, empty, or unidentified
  descriptions are excluded; an English title, requirements excerpt, or declared language cannot override the body.
  LinkedIn language checks isolate the description from page controls and the language-selector footer; captures
  containing only the job header and promotional interface text are excluded as unidentified descriptions.
  Detection is local and rule-based, including Polish with or without accents, with no network or AI/ML service.
  Ambiguous descriptions are conservatively excluded, so some valid descriptions may need review.
- A known maximum monthly salary of `200,000 RUB` or less is blocked. The comparison uses `1 USD = 87 RUB` and
  `1 GEL (lari) = 33 RUB`; annual amounts are divided by 12. When no maximum is provided, a known minimum below
  `150,000 RUB` monthly equivalent is also blocked. Missing salary bounds and unsupported currencies remain eligible.
  Thresholds refer to net pay; an unspecified gross/net basis is treated as net. Explicit structured gross markers
  are retained. The user's screening assumption estimates Russian-market net as `gross / (1 + 13/87)`,
  equivalently `gross × 87/100`: 100 gross consists of 87 net and 13 tax.
  other markets use gross as approximate net (the user's stated Georgia 1% is ignored). Explicit `hiring_country`
  or an explicit hiring statement such as `The employer will hire you in Georgia` takes precedence; otherwise
  RUB/RUR/₽ means Russia. Recognition is limited to supported country names and unambiguous hiring statements.
  Source, description language and employer office location do not establish the hiring country. Markdown shows the
  original gross and estimated net together; the filter uses that estimate without modifying stored gross amounts.
- For versions sharing a vacancy ID or URL, unequal timezone-aware posting datetimes select the newer complete
  description and conditions. Receipt, capture and fetch times do not establish posting freshness. Missing/equal dates,
  partial descriptions or unresolved conflicts retain combined evidence with a warning. Closure/application evidence
  survives this content selection. This does not change raw-store availability checks or the accepted C15 identity rule.
  Conflicting salary offers are retained in JSON and the CSV `salary_variants` column, and shown in Markdown with
  their sources; a salary blocker applies only if every offer
  can be evaluated and fails the threshold. A passing or unevaluable offer keeps the vacancy available for review.
- A remote keyword alone is insufficient: negated remote options and remote access/client/system references do not
  establish remote work. Working hours/timezone preferences impose no additional filter.
- Role relevance normally requires a target-title match.
- Description-only relevance requires at least two distinct strong BA/SA, modeling, integration/API, Atlassian administration, SQL/data, or technical documentation signal groups.
- Token-boundary matching prevents `intern` from matching `internal` and `AI` from matching ordinary words.
- There is no percentage-match threshold. All sources use shared eligibility rules and deterministic
  score/publication-date sorting.
