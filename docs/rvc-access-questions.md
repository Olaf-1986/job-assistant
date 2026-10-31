# RVC personal discovery pilot: access questions

Status: draft, not sent. Provider confirmation is pending.

To: info@rvc.global
Subject: Personal MCP job-search pilot: local retention, dates and supported enrichment

Hello RVC team,

I am evaluating RVC for a personal job-search assistant, focused on business/systems analyst roles available
from Georgia. The proposed pilot uses your documented unauthenticated MCP endpoint, one explicit search per
invocation and at most ten returned cards. Candidates are reviewed manually before entering a personal shortlist.

Could you confirm the following?

1. May a candidate retain those returned cards, identifiers and review notes locally for a two-week comparison
   with their own saved vacancies? What retention/deletion requirements apply?
2. What rate limits apply to manually triggered searches? Would one daily search or a scheduled personal digest
   connector be supported, and would either require Pro or a separate agreement?
3. Does `posted_after` filter the original publication date, RVC ingestion date or another timestamp?
   We observed a result whose detail publication date preceded the requested cutoff.
4. Are `masked_id` values stable across edits and reposts? How should clients detect closed or removed vacancies?
5. Does `eligible_countries` reflect an employer's explicit restriction or inferred regional coverage?
6. Is there an approved way to obtain full descriptions and original employer URLs for personal filtering,
   such as a feed, export or partner API? The public MCP intentionally omits these fields.
7. Are the search/detail schemas versioned, and how are clients informed about incompatible changes?

The pilot does not crawl your board, bypass access controls, submit applications or redistribute a catalog.
I would like to confirm the intended integration before introducing periodic collection or automated enrichment.

Thank you.

## References checked on 2026-10-09

- [Public MCP setup](https://app.rvc.global/for-ai)
- [MCP contract](https://app.rvc.global/for-ai.md)
- [Product brief](https://app.rvc.global/llms-full.txt)
- [Terms](https://app.rvc.global/terms)

The public material supports anonymous MCP discovery but does not explicitly resolve the retention and scheduled
use questions above. The implementation supports bounded, user-invoked discovery and local user-provided review.
The default pilot adds no scheduler, HTML crawler, account integration or automatic `get_job` fan-out.
