"""Query-shape tests for the Elasticsearch memory layer.

These assert on the *queries we send*, using a recorder in place of a cluster.
That keeps the four required capabilities (BM25, semantic_text, hybrid RRF,
aggregations) under test without needing a live Elasticsearch, so the repo is
verifiable by anyone who clones it.
"""

from __future__ import annotations

from typing import Any

from receipts.memory import Memory, load_mapping, similar_summary


class RecordingClient:
    """Captures the request bodies the Memory layer builds."""

    def __init__(self, response: dict[str, Any] | None = None) -> None:
        self.searches: list[dict[str, Any]] = []
        self.indexed: list[dict[str, Any]] = []
        self._response = response or {"hits": {"hits": []}}

    def search(self, *, index: str, body: dict[str, Any]) -> dict[str, Any]:
        self.searches.append({"index": index, "body": body})
        return self._response

    def index(self, *, index: str, document: dict[str, Any]) -> dict[str, Any]:
        self.indexed.append({"index": index, "document": document})
        return {"result": "created"}


def memory(response: dict[str, Any] | None = None) -> tuple[Memory, RecordingClient]:
    client = RecordingClient(response)
    return Memory(client, "receipts"), client


def test_keyword_search_uses_bm25_on_analyzed_field() -> None:
    mem, client = memory()
    mem.keyword_search("crypto entrepreneur")

    body = client.searches[0]["body"]
    # BM25 must hit bio_raw (text), not bio (semantic_text).
    assert body["query"]["match"]["bio_raw"]["query"] == "crypto entrepreneur"


def test_semantic_search_targets_the_semantic_text_field() -> None:
    mem, client = memory()
    mem.semantic_search("founder and digital asset trader")

    body = client.searches[0]["body"]
    semantic = body["query"]["bool"]["must"][0]["semantic"]
    assert semantic["field"] == "bio"
    assert semantic["query"] == "founder and digital asset trader"


def test_semantic_search_can_exclude_a_handle() -> None:
    mem, client = memory()
    mem.semantic_search("bio text", exclude_handle="someone")

    body = client.searches[0]["body"]
    # Without this a re-scored profile matches itself and inflates ring_score.
    assert body["query"]["bool"]["must_not"] == [{"term": {"handle": "someone"}}]


def test_semantic_search_omits_must_not_when_no_handle_given() -> None:
    mem, client = memory()
    mem.semantic_search("bio text")

    assert "must_not" not in client.searches[0]["body"]["query"]["bool"]


def test_hybrid_search_fuses_semantic_and_exact_link_with_rrf() -> None:
    mem, client = memory()
    mem.hybrid_search("reworded bio", external_url="https://linktr.ee/x")

    rrf = client.searches[0]["body"]["retriever"]["rrf"]
    assert len(rrf["retrievers"]) == 2

    kinds = [
        list(r["standard"]["query"].keys())[0] for r in rrf["retrievers"]
    ]
    assert "semantic" in kinds  # catches the rewrite
    assert "term" in kinds      # catches the reused link


def test_hybrid_search_degrades_to_semantic_only_without_a_link() -> None:
    mem, client = memory()
    mem.hybrid_search("reworded bio")

    rrf = client.searches[0]["body"]["retriever"]["rrf"]
    assert len(rrf["retrievers"]) == 1
    assert "semantic" in rrf["retrievers"][0]["standard"]["query"]


def test_ring_fingerprint_uses_significant_text_over_flagged_docs() -> None:
    mem, client = memory()
    mem.ring_fingerprint()

    body = client.searches[0]["body"]
    assert body["size"] == 0  # aggregation only, no hits
    assert body["query"] == {"term": {"is_ring": True}}
    # significant_text needs an analyzed field — bio_raw, never bio.
    assert body["aggs"]["fingerprint"]["significant_text"]["field"] == "bio_raw"


def test_creation_histogram_buckets_flagged_accounts_by_week() -> None:
    mem, client = memory()
    mem.creation_histogram()

    agg = client.searches[0]["body"]["aggs"]["created"]["date_histogram"]
    assert agg["field"] == "first_post_at"
    assert agg["calendar_interval"] == "week"


def test_gap_by_interest_uses_a_nested_aggregation() -> None:
    mem, client = memory()
    mem.gap_by_interest()

    aggs = client.searches[0]["body"]["aggs"]["claims"]
    # claims is a nested field; a terms agg on it without nesting would mix
    # interests across documents.
    assert aggs["nested"] == {"path": "claims"}
    assert "avg_claimed" in aggs["aggs"]["by_interest"]["aggs"]
    assert "avg_evidence" in aggs["aggs"]["by_interest"]["aggs"]


def test_remember_writes_to_the_configured_index() -> None:
    mem, client = memory()
    mem.remember({"handle": "x", "score": 3})

    assert client.indexed == [
        {"index": "receipts", "document": {"handle": "x", "score": 3}}
    ]


def test_similar_summary_flattens_hits_for_the_prompt() -> None:
    response = {
        "hits": {
            "hits": [
                {
                    "_score": 0.91234,
                    "_source": {
                        "handle": "a",
                        "bio_raw": "Entrepreneur",
                        "is_ring": True,
                    },
                }
            ]
        }
    }

    assert similar_summary(response) == [
        {
            "handle": "a",
            "bio": "Entrepreneur",
            "similarity": 0.912,
            "previously_flagged": True,
        }
    ]


def test_similar_summary_handles_empty_results() -> None:
    assert similar_summary({"hits": {"hits": []}}) == []
    assert similar_summary({}) == []


def test_semantic_search_rejects_empty_text() -> None:
    """Fail loudly at the boundary rather than as an opaque 400 from Elastic."""
    mem, client = memory()

    for blank in ("", "   ", "\n\t"):
        try:
            mem.semantic_search(blank)
        except ValueError as exc:
            assert "empty" in str(exc)
        else:
            raise AssertionError(f"expected ValueError for {blank!r}")

    assert client.searches == []


def test_hybrid_search_rejects_empty_text() -> None:
    mem, client = memory()

    try:
        mem.hybrid_search("")
    except ValueError as exc:
        assert "empty" in str(exc)
    else:
        raise AssertionError("expected ValueError")

    assert client.searches == []


def test_mapping_declares_both_semantic_and_analyzed_bio_fields() -> None:
    props = load_mapping()["mappings"]["properties"]

    # Both are required: semantic_text powers kNN, text powers BM25 and
    # significant_text. Dropping either breaks a capability.
    assert props["bio"]["type"] == "semantic_text"
    assert props["bio_raw"]["type"] == "text"
    assert props["handle"]["type"] == "keyword"
    assert props["claims"]["type"] == "nested"
