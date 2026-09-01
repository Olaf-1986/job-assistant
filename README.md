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
uv run python -m job_assistant shortlist --source telegram
uv run pytest
```

### Quick fetch aliases

Use the installed `qf WINDOW SOURCE SIZE` executable for a compact, composable fetch command. It is available directly
as `qf` while the project environment is active, or through `uv run qf` without activation:

```bash
qf 1d all 25
qf 2d hh 50
qf 3d li 20 --li-limit 10
qf 1w tg 30
qf 2w all 50
qf 3w hh 100
qf 1m all 50
```

Windows are `1d`, `2d`, `3d`, `1w`, `2w`, `3w`, and `1m`; sources are `all`, `hh`, `li`, and `tg`.
`SIZE` temporarily controls the generated shortlist length without editing `preferences.yaml`. `all` explicitly runs the
normal HH/email workflow followed by LinkedIn queue processing and read-only Telegram ingestion. LinkedIn is
queue-driven, so its items are not filtered by `WINDOW`; `--li-limit` controls how many pending queue items it opens
(default 5). Telegram uses the window on its initial read, while existing checkpoints still take precedence. Add
`--force` only when another successful HH fetch is intentional.
For `hh`, `li`, or `tg`, `output/shortlist.md` contains only vacancies attributed to that selected source;
`output/combined_shortlist.md` remains the canonical all-source view.

The same temporary shortlist override is also available as `fetch -n SIZE`, `fetch-all -n SIZE`,
`linkedin-fetch -n SIZE`, `telegram-fetch -n SIZE`, and `shortlist -n SIZE`.

`fetch-all` automatically fetches only HeadHunter. It then runs enabled read-only email ingestion as a separate stage
before rebuilding outputs. `fetch --source linkedin` starts neither LinkedIn workflow and makes no LinkedIn request; it
only prints instructions for extension capture and explicit `linkedin-fetch`. Playwright never starts implicitly
through `fetch` or `fetch-all`.

## Persistence

Output files under `output/` are derived data. Combined outputs are rebuilt only from:

- latest successful `output/headhunter_raw.json`;
- LinkedIn records stored in `output/manual_imports.json` by the extension, explicit `linkedin-fetch`, or explicit manual import.
- Telegram records stored in `output/telegram_raw.json` by explicit `telegram-fetch` runs.

Email ingestion also writes derived `output/email_candidates.json`, `output/linkedin_email_queue.md`, and `output/email_state.json`. These files are derived/local state and are safe to rebuild from the configured mailbox.

Telegram checkpoints and deterministic extraction failures are stored separately in `output/telegram_checkpoints.json`
and `output/telegram_failures.json`. Rebuilds do not trust old `combined_jobs.json`. Failed or skipped HeadHunter runs
do not replace valid LinkedIn or Telegram captures with empty output.

## HeadHunter

Use `--last day`, `--last 3-days`, `--last week`, or `--last 2-weeks` with `fetch` or `fetch-all` for a quick
publication-date window. HeadHunter receives the corresponding official API `period` value; on `fetch-all`, the email
alert lookback uses the same window. Omitting `--last` preserves the configured HH behavior and the existing 30-day
email lookback. LinkedIn remains queue-driven, while Telegram keeps its explicit `--since-days` option.

Andersen vacancies are temporarily blocked from eligible results through 8 November 2026 inclusive. The exclusion
expires automatically on 9 November 2026.

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

`linkedin-fetch` is opt-in queue processing, not a scheduled or automatic source. `--login` opens a headed Chromium window for manual login and stores browser state only in the dedicated local persistent profile. A normal run applies the title prefilter before navigation, uses a newly selected 5–20 second delay after each opened vacancy, blocks image/media/font requests, extracts semantic job content, and checkpoints each processed queue item. Login-required, CAPTCHA, account-restriction, or similar barrier pages stop the run and are never bypassed. An explicit rate-limit signal also stops immediately without retry, writes only sanitized local block timing to `output/linkedin_rate_limit_state.json`, and requires a separate invocation after two minutes. Successful extension and Playwright captures enter the same normalization, deduplication, filtering, scoring, persistence, and export pipeline. LinkedIn pages marked as no longer accepting applications are recorded as expired with a blocker reason.

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
- Onsite/hybrid outside Tbilisi is blocked.
- Explicit Russia-only work-location restrictions are blocked.
- Russian citizenship by itself is not blocked.
- Explicit German requirements are blocked or held for manual review.
- A known maximum monthly salary of `200,000 RUB` or less is blocked. The comparison uses `1 USD = 87 RUB` and
  `1 GEL (lari) = 33 RUB`; annual maxima are divided by 12. Unknown maxima and unsupported currencies remain eligible.
- Role relevance normally requires a target-title match.
- Description-only relevance requires at least two distinct strong BA/SA, modeling, integration/API, Atlassian administration, SQL/data, or technical documentation signal groups.
- Token-boundary matching prevents `intern` from matching `internal` and `AI` from matching ordinary words.
- The requested existing 70% match threshold is not represented in the current model. To avoid changing HH/LinkedIn semantics, Telegram reuses the implemented eligibility and deterministic score/publication-date sorting rules.
