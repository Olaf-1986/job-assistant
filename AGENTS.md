# Repository Agent Instructions

## Scope and current source model

Treat implemented behavior separately from planned or assumed behavior.

- HeadHunter is the only vacancy source executed by `fetch-all` and uses the official vacancies API.
- Email ingestion is read-only and runs from `fetch-all`.
- LinkedIn queue items support two opt-in workflows: user-triggered current-page capture through the Chromium extension, and explicitly invoked Playwright processing through `linkedin-fetch`.
- `fetch --source linkedin` starts neither workflow and makes no LinkedIn request. Neither `fetch` nor `fetch-all` may start LinkedIn Playwright.
- Telegram is an explicitly invoked, read-only Telethon workflow over configured allowlisted dialogs already joined by the account. It never runs from `fetch` or `fetch-all`.
- New sources must enter the shared normalization, deduplication, filtering, scoring, persistence, and export pipeline.

## Sources of truth

Use this precedence:

1. The user's current explicit request.
2. Current code, configuration, and tests for implemented behavior.
3. This `AGENTS.md` for repository-wide working rules.
4. `README.md` for user-facing setup, commands, and implemented behavior.
5. `job-assistant-chat-handoff.md` as the single current technical-state handoff.
6. `specs/archive/` only as historical, non-authoritative material; archived documents are not active scope.

Call out contradictions instead of silently following an obsolete document or inventing behavior.

## Required workflow

- Inspect `git status` and relevant complete diffs before editing. Preserve unrelated changes and every untracked file.
- Inspect existing tests before changing behavior. Make minimal, focused changes and preserve established interfaces unless the request explicitly changes them.
- Use the uv-managed environment, normally through `uv run ...`.
- In reports, distinguish implemented, planned, and assumed behavior.
- When staging is requested, use explicit paths. Never infer permission to commit or push.

## Verification

Run targeted tests while developing. Unless the task narrows verification, finish with:

```bash
uv run pytest -q
uv run python -m job_assistant validate-config
git diff --check
```

Tests must not use live network or IMAP access, browser sessions, credentials, `.env` values, or persistent browser profiles. Mock external behavior or exercise it with local fixtures.

## Persistence and pipeline invariants

- Preserve the implemented pipeline order: normalize, deduplicate, filter, score, persist/export. New ingestion paths must use this shared pipeline rather than writing final combined outputs independently.
- Rebuild derived combined outputs only from authoritative stores: the latest successful HeadHunter raw data, persisted LinkedIn captures/imports, and persisted Telegram raw records. Never treat an old combined output as an authoritative input.
- Preserve idempotency for repeated email ingestion, duplicate URLs/job IDs, repeated LinkedIn processing, and Telegram message/vacancy identities.
- Preserve compatibility with existing persisted records, including the legacy LinkedIn `manual_capture_required` queue status.
- Never silently lose or destructively replace successful stored data. Failed or skipped HeadHunter/email work must not erase valid HeadHunter, LinkedIn, or Telegram data; HeadHunter refreshes must preserve LinkedIn and Telegram captures.
- Use the repository's existing persistence and write helpers and preserve their current safe-write behavior. Do not claim general atomic writes unless current code and tests establish them; atomic persistence is not currently a documented repository-wide guarantee.

## Security and filesystem boundaries

- Never open, read, or inspect `.env` contents. Use documented environment-variable names instead. Never request, print, commit, or export secrets or `.env` values.
- Never expose credentials, OAuth or capture tokens, verification codes, 2FA passwords, cookies, browser profiles, Telegram sessions, or similar authentication material.
- Never include generated vacancy data or user mailbox contents in commits or reports.
- LinkedIn login requirements, CAPTCHA, account restrictions, and similar barriers must stop processing and must never be bypassed. Playwright may use only its dedicated local persistent profile.
- Reading outside the repository is allowed when required. Writing, editing, moving, or deleting outside the repository is forbidden.
- Do not make live external requests unless the user explicitly requests the relevant operation.

## Git safety

- Do not use destructive Git operations. Do not reset, checkout-overwrite, rebase, merge, or cherry-pick without explicit need and review.
- Never use `git add .`, `git add -A`, or broad globs. Stage only explicit reviewed paths.
- Do not commit or push unless the user requests it.
- Never stage generated outputs, logs, caches, credentials, browser data, or unrelated files.
