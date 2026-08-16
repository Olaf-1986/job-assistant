# Phase 2: production-quality vacancy sources

You are working inside the existing project:

/home/olaf_1986/projects/job-assistant

The project already contains:

- a normalized vacancy model;
- YAML configuration;
- deterministic role relevance, blockers, scoring and deduplication;
- Himalayas and Jobicy connectors;
- CLI commands and pytest tests.

Inspect the repository before making changes.

Do not rewrite working modules unnecessarily. Extend the existing architecture.

==================================================
1. GOAL
==================================================

Replace the weak remote-job-board focus with a multi-source vacancy pipeline suitable for:

- Business Analyst;
- System Analyst / Systems Analyst;
- Business Systems Analyst;
- Technical Business Analyst;
- Requirements Analyst;
- Product Analyst;
- Implementation Analyst;
- Solutions Analyst;
- Integration Analyst;
- API Integration Analyst;
- Jira Administrator;
- Confluence Administrator;
- Jira/Confluence Administrator;
- Atlassian Administrator;
- Atlassian Consultant;
- Jira Consultant;
- Confluence Consultant;
- Product Manager as a low-priority role.

Implement or prepare these sources:

1. HeadHunter;
2. Jooble;
3. Arbeitnow;
4. Greenhouse;
5. Lever;
6. Habr Career;
7. Wellfound manual import.

Keep Himalayas and Jobicy in the codebase but disable them by default.

Do not add LLM calls, cover-letter generation, browser automation,
automatic applications, Selenium or Playwright in this phase.

==================================================
2. SOURCE MODES
==================================================

Use three explicit source modes:

- api_search:
  HeadHunter, Jooble.

- api_feed:
  Arbeitnow, Greenhouse, Lever.

- manual_import:
  Wellfound and Habr Career fallback, plus any unsupported platform.

Habr Career may later become an authenticated API source, but its automatic
connector must remain disabled until valid OAuth credentials and application
activation are available.

Never pretend that a source has a public API when it does not.

Never invent undocumented endpoints.

==================================================
3. COMMON SEARCH QUERIES
==================================================

Move the query list into reusable YAML configuration.

Primary English queries:

- Business Analyst
- System Analyst
- Systems Analyst
- Business Systems Analyst
- Technical Business Analyst
- Requirements Analyst
- Product Analyst
- Implementation Analyst
- Solutions Analyst
- Integration Analyst
- API Integration Analyst
- Jira Administrator
- Confluence Administrator
- Jira Confluence Administrator
- Atlassian Administrator
- Atlassian Consultant
- Jira Consultant
- Confluence Consultant

Primary Russian queries:

- Бизнес-аналитик
- Бизнес аналитик
- Системный аналитик
- Бизнес-системный аналитик
- Аналитик требований
- Аналитик интеграций
- Интеграционный аналитик
- Администратор Jira
- Администратор Confluence
- Администратор Atlassian
- Консультант Atlassian

Low-priority queries:

- Product Manager
- Менеджер продукта
- Продуктовый менеджер

Product Owner must not be used as a search query.

A Product Owner vacancy found indirectly is not a hard blocker, but remains
lower priority according to the existing configurable title adjustment.

==================================================
4. ROLE RELEVANCE
==================================================

Keep the role-relevance gate before scoring.

A vacancy is role-relevant when either:

A. Its title clearly matches a configured target title;

OR

B. Its description contains at least two distinct strong signal groups:

- BA/SA requirements work;
- BPMN/UML/process or systems modelling;
- Jira/Confluence/Atlassian administration;
- systems integration or API analysis;
- structured technical/process documentation;
- SQL/data analysis relevant to an analyst role.

Do not make a vacancy relevant based only on:

- AI;
- English language;
- stakeholder management;
- reporting;
- documentation alone;
- the word “product”;
- generic collaboration wording.

Use word boundaries or token-aware matching.

Do not match:

- intern inside internal;
- analyst inside an unrelated longer token;
- AI inside ordinary words;
- Jira/Confluence mentions that are only part of a generic tool list as
  administrator-level experience.

Preserve a role_relevance_breakdown field explaining why the vacancy passed
or failed the gate.

==================================================
5. HEADHUNTER CONNECTOR
==================================================

Use the official HeadHunter vacancies API.

Base API:

https://api.hh.ru

Use the official vacancy search and vacancy detail endpoints.

Requirements:

- support OAuth token through environment variables;
- send the required descriptive HH-User-Agent header;
- never store credentials in Git;
- do not attempt to bypass CAPTCHA;
- when unauthenticated access produces CAPTCHA or access restrictions,
  fail clearly and explain that OAuth configuration is required;
- use per_page up to 100;
- use official dictionaries/endpoints rather than guessing IDs;
- fetch full vacancy details for records that pass a cheap title prefilter;
- support host configuration.

Configure two HeadHunter search groups.

A. Russian-speaking and international remote search:

- host: hh.ru;
- remote vacancies regardless of region;
- English and Russian queries;
- no timezone filter.

B. Georgia/Tbilisi search:

- host: headhunter.ge;
- Tbilisi and Georgia;
- remote, hybrid and onsite;
- hybrid/onsite accepted only in Tbilisi;
- English and Russian queries.

Where practical, search vacancy titles first rather than all description text
to reduce irrelevant results.

Use official work format, area, experience and professional-role dictionaries.
Do not hard-code dictionary IDs without validating them.

Store:

- vacancy ID;
- alternate URL;
- apply URL if supplied;
- employer;
- area;
- work format;
- experience;
- employment;
- salary;
- key skills;
- description;
- published date;
- response_letter_required.

==================================================
6. JOOBLE CONNECTOR
==================================================

Official endpoint pattern:

POST https://jooble.org/api/{API_KEY}

Read the API key from:

JOOBLE_API_KEY

If the key is absent:

- skip Jooble cleanly;
- record source status as disabled_missing_credentials;
- do not fail the whole multi-source run.

For every configured target query, search these locations:

- Remote
- Tbilisi
- Georgia

Use:

- companysearch: false;
- configurable page count;
- configurable ResultOnPage;
- one daily batch only.

Normalize:

- id;
- title;
- company;
- location;
- snippet;
- salary;
- job type;
- source;
- updated timestamp;
- source link.

Jooble often supplies only a snippet. Do not scrape the destination site to
obtain the full description.

Mark records with incomplete descriptions for manual review.

==================================================
7. ARBEITNOW CONNECTOR
==================================================

Use:

GET https://www.arbeitnow.com/api/job-board-api

No API key is required.

The API is a general feed rather than a reliable keyword-search endpoint.

Requirements:

- fetch a configurable number of pages;
- filter locally by title and role relevance;
- preserve the remote and visa_sponsorship fields;
- do not require visa sponsorship;
- do not make Germany a hard blocker for remote roles;
- onsite/hybrid outside Tbilisi remains blocked;
- support pagination from the API response rather than guessing URLs.

Add relevant German title aliases for local title detection:

- Systemanalyst
- Prozessanalyst
- Anforderungsmanager
- Requirements Engineer
- Business Process Analyst
- IT-Prozessmanager
- Applikationsmanager
- Jira Administrator
- Atlassian Administrator

A German-language vacancy requiring German must be marked for manual review
or blocked by an explicit language-requirement rule, not incorrectly detected
as English.

==================================================
8. GREENHOUSE CONNECTOR
==================================================

Greenhouse is company-specific, not a global vacancy search engine.

Use the public Job Board API:

GET https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs?content=true

GET requests require no authentication.

Create:

config/target_companies.yaml

Structure:

greenhouse:
  companies:
    - name: Company Name
      board_token: company-token
      enabled: false

Do not add invented companies or tokens.

For each enabled company:

- fetch all published jobs;
- retain offices and departments when supplied;
- convert HTML descriptions to readable text;
- apply the common title and role-relevance filter locally;
- retain the Greenhouse application URL;
- record company and board token provenance.

An empty company list is valid and must cause a clean skip.

==================================================
9. LEVER CONNECTOR
==================================================

Lever is also company-specific.

Global API:

GET https://api.lever.co/v0/postings/{site}?mode=json

EU API:

GET https://api.eu.lever.co/v0/postings/{site}?mode=json

Extend config/target_companies.yaml:

lever:
  companies:
    - name: Company Name
      site: company-site
      region: global
      enabled: false

Supported regions:

- global;
- eu.

Do not add invented company site identifiers.

Fetch all public postings for each enabled company and apply local filtering.

Normalize:

- posting ID;
- title;
- description;
- lists;
- location;
- all locations;
- commitment;
- team;
- department;
- workplace type where supplied;
- hosted application URL.

Do not try to perform global full-text search through Lever.

==================================================
10. HABR CAREER
==================================================

Habr Career has an official API but requires:

- application registration;
- application activation;
- OAuth 2.0.

Its automatic API connector must be disabled by default.

Create configuration and interface scaffolding for:

HABR_CLIENT_ID
HABR_CLIENT_SECRET
HABR_ACCESS_TOKEN
HABR_REDIRECT_URI

Rules:

- do not scrape career.habr.com;
- do not guess API endpoints;
- use only endpoints confirmed by the official API documentation;
- do not run Habr API requests on a schedule;
- requests must be explicitly initiated by the user;
- do not persist raw Habr API responses;
- do not implement the connector as active until credentials and supported
  vacancy methods have been verified;
- document application registration and activation requirements.

For this phase, implement a manual import fallback:

uv run python -m job_assistant import-manual \
  --source habr_career \
  --url "<vacancy URL>" \
  --file "<text file>"

The command must not fetch the URL. It must process only the text explicitly
provided by the user.

==================================================
11. WELLFOUND
==================================================

Do not implement an automated Wellfound connector.

Do not:

- scrape Wellfound;
- call internal or undocumented APIs;
- automate login;
- read browser cookies;
- use Selenium or Playwright;
- crawl search results;
- automatically retrieve a vacancy page.

Implement manual import only:

uv run python -m job_assistant import-manual \
  --source wellfound \
  --url "<vacancy URL>" \
  --file "<text file>"

The user manually opens the vacancy and supplies its text.

The command must:

- preserve the original URL;
- normalize the provided text;
- identify title and company from explicit CLI options or the text;
- apply role relevance, blockers and scoring;
- add the vacancy to the combined dataset;
- never make an HTTP request to Wellfound.

Support explicit optional arguments:

--title
--company
--language

==================================================
12. MANUAL IMPORT GENERALLY
==================================================

The manual import command must support:

- wellfound;
- habr_career;
- linkedin;
- other.

Example:

uv run python -m job_assistant import-manual \
  --source wellfound \
  --url "https://wellfound.com/..." \
  --file input/vacancy.txt \
  --title "Business Systems Analyst" \
  --company "Company Name"

Create:

input/manual/.gitkeep

Do not store browser cookies, passwords or session data.

==================================================
13. MULTI-SOURCE CONFIGURATION
==================================================

Add source configuration similar to:

sources:
  headhunter:
    enabled: true
    mode: api_search
    manual_run_only: false

  jooble:
    enabled: true
    mode: api_search
    requires_env:
      - JOOBLE_API_KEY

  arbeitnow:
    enabled: true
    mode: api_feed
    pages_to_fetch: 10

  greenhouse:
    enabled: true
    mode: api_feed
    company_config: config/target_companies.yaml

  lever:
    enabled: true
    mode: api_feed
    company_config: config/target_companies.yaml

  habr_career:
    enabled: false
    mode: authenticated_api
    manual_run_only: true

  wellfound:
    enabled: true
    mode: manual_import

  himalayas:
    enabled: false

  jobicy:
    enabled: false

Do not store secrets in YAML.

Use .env only for local credentials and ensure .env is ignored by Git.

Create .env.example without real credential values.

==================================================
14. CLI
==================================================

Support:

uv run python -m job_assistant fetch --source headhunter
uv run python -m job_assistant fetch --source jooble
uv run python -m job_assistant fetch --source arbeitnow
uv run python -m job_assistant fetch --source greenhouse
uv run python -m job_assistant fetch --source lever

Combined run:

uv run python -m job_assistant fetch-all

Manual import:

uv run python -m job_assistant import-manual ...

Source status:

uv run python -m job_assistant sources

The sources command must show:

- enabled/disabled;
- API/feed/manual mode;
- credential status;
- company-list status;
- last successful run;
- last error.

The tool must enforce at most one successful automatic fetch per source per
calendar day unless the user explicitly passes:

--force

The --force flag must print a warning.

Do not apply the daily restriction to local rebuilds from cached data or to
manual imports.

==================================================
15. NORMALIZATION AND DEDUPLICATION
==================================================

Extend the normalized model with:

- source;
- source_id;
- source_url;
- apply_url;
- source_queries;
- source_company_identifier;
- description_completeness;
- imported_manually;
- source_metadata;
- first_seen_at;
- last_seen_at.

Deduplicate across sources conservatively:

1. canonical application URL;
2. canonical source URL;
3. source-specific stable ID;
4. normalized company + title + location;
5. cautious similarity only when publication dates and descriptions support it.

Do not merge different vacancies merely because company and title are similar.

When duplicates are merged:

- retain all source names and URLs;
- prefer the richest description;
- retain the most direct employer application URL;
- retain all query provenance;
- preserve source-specific IDs.

==================================================
16. OUTPUTS
==================================================

Keep existing outputs and add:

output/combined_jobs.json
output/combined_jobs.csv
output/combined_shortlist.md
output/source_status.json
output/manual_imports.json

The combined shortlist must show:

- rank;
- score;
- role relevance explanation;
- title;
- company;
- work format;
- location;
- language;
- salary;
- publication/update date;
- description completeness;
- source or sources;
- direct vacancy URL;
- application URL;
- warnings;
- score breakdown.

Do not award score merely because a vacancy came from a preferred source.

Source quality may be used only as a final tie-breaker.

==================================================
17. ERROR ISOLATION
==================================================

Failure of one source must not fail the entire combined run.

For each source record:

- success;
- skipped;
- missing_credentials;
- empty_company_list;
- rate_limited;
- authentication_required;
- captcha_required;
- HTTP error;
- parsing error;
- unsupported;
- terms_restricted.

Never hide errors behind an empty result.

==================================================
18. TESTING
==================================================

Tests must use fixtures and mocked HTTP responses.

Do not repeatedly call live APIs in pytest.

Add fixtures for:

- HeadHunter Russian BA vacancy;
- HeadHunter Tbilisi hybrid vacancy;
- HeadHunter unrelated developer vacancy;
- Jooble result with incomplete description;
- Arbeitnow relevant analyst vacancy;
- Arbeitnow German unrelated role;
- Greenhouse company feed;
- Lever global feed;
- Lever EU feed;
- empty target company lists;
- missing Jooble credentials;
- HeadHunter CAPTCHA/authentication response;
- manual Wellfound import;
- manual Habr Career import;
- cross-source duplicate;
- two similar but distinct vacancies from the same company.

Test:

- source-specific normalization;
- role relevance;
- title aliases;
- location rules;
- manual import performs no HTTP request;
- disabled Wellfound automation;
- Habr automatic connector remains disabled without credentials;
- cross-source deduplication;
- failure isolation;
- one-run-per-day guard;
- --force behavior;
- deterministic combined ordering.

==================================================
19. DOCUMENTATION
==================================================

Update README with:

- supported sources;
- source modes;
- exact CLI commands;
- API credential setup;
- Jooble API key setup;
- HeadHunter OAuth requirements;
- Greenhouse board token configuration;
- Lever site configuration;
- Habr Career activation limitations;
- Wellfound manual-only limitation;
- daily-run rules;
- troubleshooting;
- statement that no automatic application is performed.

Do not claim that Greenhouse or Lever provide global job search.

Do not claim that Wellfound has a supported public API.

Do not claim that the Habr connector is active before OAuth setup and
official endpoint verification.

==================================================
20. EXECUTION ORDER
==================================================

Implement in this order:

1. shared source configuration and status model;
2. HeadHunter;
3. Jooble;
4. Arbeitnow;
5. Greenhouse;
6. Lever;
7. generic manual import;
8. Wellfound manual source;
9. Habr Career disabled scaffold and manual fallback;
10. combined deduplication and exports;
11. tests and documentation.

Run live requests only for sources that:

- are enabled;
- have the required credentials/configuration;
- have not already completed a successful run today.

Do not make live requests to Wellfound.

Do not make live Habr Career requests in this phase.

==================================================
21. ACCEPTANCE CRITERIA
==================================================

Complete only when these work:

uv run python -m job_assistant validate-config
uv run python -m job_assistant sources
uv run python -m job_assistant --help
uv run pytest

Where credentials/configuration permit:

uv run python -m job_assistant fetch --source headhunter
uv run python -m job_assistant fetch --source jooble
uv run python -m job_assistant fetch --source arbeitnow
uv run python -m job_assistant fetch --source greenhouse
uv run python -m job_assistant fetch --source lever
uv run python -m job_assistant fetch-all

Also test manual import using a local fixture file.

At the end report:

- files changed;
- dependencies added;
- tests passed;
- source statuses;
- live source statistics;
- credentials or company lists still required;
- assumptions;
- limitations.

Do not proceed to cover-letter generation or LLM integration.

Stop after the source layer and combined shortlist work.
