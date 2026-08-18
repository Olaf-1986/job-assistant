from __future__ import annotations

from job_assistant.role_relevance import title_prefilter_matches


def test_title_prefilter_matches_uses_phrase_boundaries():
    assert title_prefilter_matches("Internal Tools Engineer", ["intern"]) is False
    assert title_prefilter_matches("Business Analyst", ["business analyst"]) is True
