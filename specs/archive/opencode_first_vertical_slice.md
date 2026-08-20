# Archived: First Vertical Slice

> Historical specification: Himalayas, Working Nomads, Built In, Dynamite Jobs, and Glassdoor are deprecated legacy sources removed from active code and configuration. References below describe the original implementation plan only.

You are working inside the existing local Python project:

/home/olaf_1986/projects/job-assistant

Implement the first working vertical slice of a local, single-user job-search assistant.

Before changing files:
1. Inspect the current repository and pyproject.toml.
2. Preserve the existing uv-managed environment and dependencies.
3. Do not modify global Python, WSL, system settings, or files outside this repository.
4. Then implement the solution without asking follow-up questions unless a genuinely blocking ambiguity appears.

==================================================
1. CURRENT SCOPE
==================================================

Implement only:

Himalayas Remote Jobs API
→ fetch relevant vacancies
→ normalize data
→ apply deterministic hard filters and scoring
→ deduplicate
→ export raw data, normalized data, CSV, and Markdown shortlist.

Do not implement yet:

- OpenAI or any other LLM calls;
- cover-letter generation;
- LinkedIn access;
- browser automation;
- Selenium or Playwright;
- scraping;
- automated applications;
- email or Telegram integration;
- PostgreSQL, vector databases, or embeddings;
- scheduling or cron.

This version will be launched manually. It must be designed so that it can later be scheduled once per day.

==================================================
2. TECHNOLOGY AND PROJECT RULES
==================================================

Use:

- Python 3.12;
- uv for dependency and environment management;
- httpx;
- pydantic;
- pyyaml;
- beautifulsoup4;
- rich;
- typer;
- pandas only where it is genuinely useful.

You may add pytest as a development dependency using uv if needed.

Requirements:

- use a src layout;
- use type hints;
- use pathlib instead of hard-coded path strings;
- add reasonable timeouts and error handling;
- keep connector-specific logic isolated;
- keep all preferences and scoring rules editable in YAML;
- do not hard-code user preferences inside Python modules;
- do not silently ignore malformed API records;
- log warnings and continue where possible;
- use UTF-8 everywhere;
- preserve both Russian and English text correctly;
- do not require activation of .venv: commands must work through `uv run`.

==================================================
3. HIMALAYAS API
==================================================

Use the official search endpoint:

https://himalayas.app/jobs/api/search

The API is public and requires no authentication.

Use configured search queries rather than crawling unrelated vacancies.

The connector must:

- send requests through httpx;
- use a clear User-Agent;
- use a finite timeout;
- retry transient failures such as 429 and 5xx with limited exponential backoff;
- never retry indefinitely;
- store the raw response data;
- handle sanitized HTML descriptions;
- convert HTML descriptions into readable plain text while preserving paragraph boundaries and bullet-like structure;
- retrieve only the pages configured in preferences.yaml;
- run as one daily batch conceptually, with no continuous polling.

Relevant response fields may include:

- title;
- excerpt;
- companyName;
- companySlug;
- employmentType;
- minSalary;
- maxSalary;
- salaryPeriod;
- currency;
- seniority;
- locationRestrictions;
- timezoneRestrictions;
- categories;
- parentCategories;
- description;
- pubDate;
- expiryDate;
- applicationLink;
- guid.

Do not assume that every field is present or non-null.

==================================================
4. SEARCH QUERIES
==================================================

Create these default primary queries in config/preferences.yaml:

- Business Analyst
- Systems Analyst
- System Analyst
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

Create this low-priority query:

- Product Manager

Explicitly exclude Product Owner as a target title.

Search queries must remain configurable in YAML.

For the first test version:

- fetch the first page for each query;
- make the number of pages per query configurable;
- deduplicate records returned by multiple queries;
- retain information about every query through which a vacancy was found.

==================================================
5. INTERNAL DATA MODEL
==================================================

Create a Pydantic model representing a normalized vacancy.

Include at least:

- source;
- source_id;
- source_queries;
- title;
- normalized_title;
- company;
- company_slug;
- description_html;
- description_text;
- excerpt;
- employment_type;
- seniority;
- categories;
- location_restrictions;
- timezone_restrictions;
- salary_min;
- salary_max;
- salary_currency;
- salary_period;
- publication_date;
- expiry_date;
- application_url;
- fetched_at;
- detected_language;
- work_mode;
- detected_city;
- blocker;
- blocker_reasons;
- score;
- score_breakdown;
- matched_signals;
- warnings.

Use nullable fields where appropriate.

==================================================
6. LANGUAGE AND LOCATION RULES
==================================================

Accepted vacancy languages:

- English;
- Russian.

Do not require worldwide availability.

Do not filter or penalize based on timezone.

Remote vacancies are allowed regardless of timezone or regional wording unless another hard blocker applies.

Onsite and hybrid vacancies:

- allowed only when the workplace is in Tbilisi;
- recognize at least:
  - Tbilisi;
  - Тбилиси;
  - Georgia, Tbilisi;
  - Tbilisi, Georgia;
- onsite or hybrid outside Tbilisi is a hard blocker.

An English-language or clearly international English-speaking role receives +15.

A Russian-language vacancy receives no penalty.

Language detection in this deterministic version may be heuristic and must be isolated in a replaceable function.

==================================================
7. HARD BLOCKERS
==================================================

Mark a vacancy as blocked, but preserve it in normalized output, when any of the following applies:

1. Pure software developer or engineering role without a meaningful BA, SA, product-analysis, requirements, Jira/Confluence administration, or integration-analysis component.

2. Pure sales role.

3. Pure customer-support role.

4. Onsite or hybrid work outside Tbilisi.

5. The vacancy explicitly requires citizenship of a specific country other than Russia.

6. The vacancy explicitly requires work authorization in a country other than Georgia and provides no remote-compatible alternative.

Important:

- lack of current Georgian work authorization is NOT a blocker;
- the user can obtain Georgian work authorization relatively quickly;
- Georgian work authorization requirements should be shown as a warning or informational signal, not as a blocker;
- generic statements such as “must be legally eligible to work” should not automatically block a remote vacancy unless the required country is clear;
- uncertain cases must be marked for manual review rather than automatically blocked.

Do not block or penalize adult-industry vacancies.

Crypto or gambling-related vacancies are not blocked, but receive -10.

==================================================
8. DETERMINISTIC SCORING
==================================================

Apply scoring only after hard-blocker detection.

Blocked vacancies must retain a calculated score for diagnostics, but must not appear in the main shortlist.

Create editable YAML scoring rules with these defaults:

+40:
Jira, Confluence, or Atlassian administration is a central responsibility.
Examples:
- administration;
- workflows;
- schemes;
- permissions;
- roles;
- custom fields;
- automation;
- user management;
- migrations;
- integrations;
- spaces;
- access control;
- system configuration.

+20:
Jira, Confluence, or Atlassian is required mainly as a user or analytical/project tool rather than as an administrator.

Do not add both +40 and +20 for the same Jira/Confluence signal.
Use the strongest applicable level.

+20:
BPMN, UML, process modelling, business-process modelling, systems modelling, or equivalent Russian terms.

+20:
Core BA/SA work:
- requirements gathering;
- requirements analysis;
- user stories;
- use cases;
- acceptance criteria;
- functional requirements;
- non-functional requirements;
- stakeholder management;
- business analysis;
- systems analysis.

+15:
Systems integrations, inter-system interaction, cross-functional technical analysis, or liaison work between business and development teams.

+15:
Documentation-heavy responsibility:
- BRD;
- FRD;
- SRS;
- specifications;
- technical assignments;
- knowledge bases;
- user manuals;
- process documentation;
- maintaining technical documentation.

+15:
English-language or clearly international English-speaking environment.

+10:
SQL, JQL, PostgreSQL, MS SQL Server, reporting, dashboards, Tableau, Power BI, or significant data-analysis work.

+10:
Fintech, payments, payment gateways, ERP, enterprise internal systems, document-management systems, or comparable domain experience.

+10:
AI, LLM, generative AI, AI-assisted documentation, or AI-productivity responsibilities, provided the role is not a pure AI-engineering position.

+5:
REST, SOAP, API contracts, OpenAPI, Swagger, Postman, JSON, XML, event-driven integrations, Kafka, RabbitMQ, or Redpanda.

-10:
Crypto or gambling domain.

-15:
Junior role.

-25:
Internship or intern role.

0:
Product-heavy role. Do not penalize it.

Product Manager:

- keep as a low-priority target;
- do not block it;
- add no automatic title bonus merely because the title is Product Manager;
- score it based on actual responsibilities and relevant signals.

Product Owner:

- exclude as a target title;
- if returned through another query, do not automatically block it;
- apply a configurable negative title adjustment so it ranks below relevant BA/SA roles.

Avoid double-counting substantially identical signals.
Store a transparent score breakdown for every vacancy.

==================================================
9. DEDUPLICATION
==================================================

Deduplicate in this order:

1. same source GUID or stable source ID;
2. same canonical application URL;
3. normalized company + normalized title;
4. cautious fallback similarity only if clearly safe.

When records are merged:

- preserve all source queries;
- preserve the richest description;
- preserve warnings;
- do not lose application links or source identifiers.

Do not use embeddings or an LLM.

==================================================
10. OUTPUTS
==================================================

Create these output files:

output/himalayas_raw.json
output/jobs_normalized.json
output/jobs.csv
output/shortlist.md
output/blocked_jobs.md
output/run_summary.json

The main shortlist must:

- include up to 50 non-blocked vacancies;
- sort by score descending;
- use publication date as a secondary sort where available;
- show for each vacancy:
  - rank;
  - score;
  - title;
  - company;
  - employment type;
  - language;
  - location restrictions;
  - salary, if available;
  - publication date;
  - matched signals;
  - concise score breakdown;
  - warnings;
  - application URL;
  - source attribution to Himalayas.

blocked_jobs.md must show:

- title;
- company;
- score;
- blocker reasons;
- application URL.

run_summary.json must include:

- run timestamp;
- source;
- queries executed;
- requests made;
- raw records received;
- normalized records;
- duplicates merged;
- blocked count;
- eligible count;
- shortlist count;
- warning count;
- errors.

==================================================
11. CONFIGURATION
==================================================

Create config/preferences.yaml containing at least:

run:
  shortlist_size: 50
  pages_per_query: 1
  request_timeout_seconds: 30
  request_delay_seconds: 0.5
  max_retries: 3

future_llm:
  max_analyzed_jobs_per_day: 20
  enabled: false

languages:
  accepted:
    - en
    - ru
  international_english_bonus: 15
  russian_penalty: 0

location:
  remote_allowed: true
  timezone_filter_enabled: false
  onsite_hybrid_allowed_cities:
    - Tbilisi
    - Тбилиси

The rest of the title rules, blockers, keyword groups, scoring weights, and output settings must also be represented in YAML rather than buried in Python.

Do not include a max_letters_per_day parameter.

Validate the YAML configuration with Pydantic models.
Fail clearly if the configuration is structurally invalid.

==================================================
12. CLI
==================================================

Implement a Typer CLI that works through uv.

Required commands:

uv run python -m job_assistant fetch --source himalayas

This command should:

- fetch;
- save raw data;
- normalize;
- deduplicate;
- filter;
- score;
- export all outputs;
- print a concise Rich summary.

Also support:

uv run python -m job_assistant shortlist

This command should rebuild the shortlist and related exports from the previously saved normalized JSON without calling the API again.

Also support:

uv run python -m job_assistant validate-config

This command should validate preferences.yaml and print either a success message or actionable validation errors.

Add:

uv run python -m job_assistant --help

==================================================
13. EXPECTED PROJECT STRUCTURE
==================================================

Use a clean structure similar to:

config/
  preferences.yaml

output/
  .gitkeep

src/
  job_assistant/
    __init__.py
    __main__.py
    cli.py
    config.py
    models.py
    normalize.py
    filters.py
    scoring.py
    deduplicate.py
    export.py
    language.py
    location.py
    utils.py
    connectors/
      __init__.py
      base.py
      himalayas.py

tests/
  fixtures/
  test_config.py
  test_normalize.py
  test_filters.py
  test_scoring.py
  test_deduplicate.py

README.md
.gitignore

Adjust this only when there is a clear technical reason.

==================================================
14. TESTING
==================================================

Add pytest as a uv development dependency if it is not present.

Tests must not depend on the live API.

Create small local fixtures representing:

- a strong Jira/Confluence Administrator vacancy;
- a normal Business Analyst vacancy;
- a pure developer vacancy;
- an onsite vacancy outside Tbilisi;
- a hybrid vacancy in Tbilisi;
- an English international role;
- a Russian-language role;
- a Georgian work-authorization requirement;
- a non-Georgian foreign citizenship requirement;
- a junior role;
- an internship;
- a crypto role;
- duplicate records returned by two search queries;
- malformed or partially missing API fields.

Test at least:

- configuration validation;
- HTML-to-text normalization;
- hard blockers;
- Georgian work authorization not being blocked;
- Jira admin +40 versus Jira user +20;
- no double-counting of those Jira levels;
- language bonus;
- scoring breakdown;
- deduplication;
- shortlist exclusion of blocked vacancies;
- deterministic output ordering.

==================================================
15. DOCUMENTATION
==================================================

Create a concise README that explains:

- what this first version does;
- what it explicitly does not do;
- installation through uv;
- exact commands;
- output files;
- how to edit preferences.yaml;
- how scoring and blockers work;
- that the tool should currently be run at most once per day;
- that Himalayas must be named as the vacancy-data source;
- troubleshooting for HTTP errors and invalid YAML.

Do not write an oversized architectural document.

==================================================
16. ACCEPTANCE CRITERIA
==================================================

The implementation is complete only when all of these work:

1. `uv run python -m job_assistant validate-config`
2. `uv run python -m job_assistant --help`
3. `uv run pytest`
4. `uv run python -m job_assistant fetch --source himalayas`
5. `uv run python -m job_assistant shortlist`

The fetch command must create all expected output files.

The shortlist must:

- contain no hard-blocked vacancies;
- contain no more than 50 entries;
- provide visible scoring reasons;
- contain working application links when supplied by the API;
- preserve source attribution;
- be readable in both Windows and Linux editors.

At the end:

1. run the tests;
2. run config validation;
3. run the live Himalayas fetch once;
4. report:
   - files created or modified;
   - commands executed;
   - test results;
   - fetch statistics;
   - any assumptions or remaining limitations.

Do not proceed to LLM integration, LinkedIn, or cover letters after finishing this task. Stop after the first vertical slice is working.
