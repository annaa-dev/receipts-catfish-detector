"""Elasticsearch as the agent's memory and context layer.

This module is the reason the project needs Elasticsearch rather than a plain
database, and it exercises all four capabilities the hack night calls out:

  keyword (BM25)   -> `keyword_search`   exact terms: handles, URLs, hashtags
  semantic_text    -> `semantic_search`  same meaning, different words
  hybrid (RRF)     -> `hybrid_search`    fuses both in one query
  aggregations     -> `ring_fingerprint`, `creation_histogram` (Kibana-backed)

`recall()` is the read half of memory: before the model judges a profile, it
retrieves what has been seen before, and that context becomes part of the
prompt. `remember()` is the write half. A single profile cannot be "fake" on its
own — fakery is a pattern across accounts, which is exactly what a corpus plus
vector search can see and a stateless call cannot.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol

MAPPING_PATH = Path(__file__).resolve().parents[2] / "infra" / "mappings.json"


class SearchClient(Protocol):
    """The slice of the Elasticsearch client this module uses.

    Narrow on purpose: tests substitute a recorder to assert on query shape
    without a live cluster.
    """

    def search(self, *, index: str, body: dict[str, Any]) -> dict[str, Any]: ...
    def index(
        self, *, index: str, document: dict[str, Any], id: str | None = None
    ) -> dict[str, Any]: ...


def load_mapping() -> dict[str, Any]:
    """Read the index mapping from infra/ so Dev Tools and code cannot drift."""
    return json.loads(MAPPING_PATH.read_text())


class Memory:
    """Read/write interface over the `receipts` index."""

    def __init__(self, client: SearchClient, index: str = "receipts") -> None:
        self.client = client
        self.index = index

    # ---------------------------------------------------------------- retrieval

    def keyword_search(self, text: str, *, size: int = 5) -> dict[str, Any]:
        """BM25 over the analyzed bio.

        Kept distinct from semantic search because exact tokens matter: a shared
        external link, an identical handle pattern, a specific emoji sequence.
        Vectors blur precisely the details that identify a reused template.
        """
        return self.client.search(
            index=self.index,
            body={
                "size": size,
                "query": {"match": {"bio_raw": {"query": text}}},
            },
        )

    def semantic_search(
        self, text: str, *, exclude_handle: str | None = None, size: int = 5
    ) -> dict[str, Any]:
        """kNN over `semantic_text` embeddings.

        `exclude_handle` keeps a profile from matching itself, which matters
        because recall runs before the new document is written on a re-score.
        """
        if not text.strip():
            raise ValueError(
                "semantic_search needs non-empty text: the inference endpoint "
                "rejects empty input with a 400. Guard on the caller side."
            )
        must: list[dict[str, Any]] = [
            {"semantic": {"field": "bio", "query": text}},
        ]
        query: dict[str, Any] = {"bool": {"must": must}}
        if exclude_handle:
            query["bool"]["must_not"] = [{"term": {"handle": exclude_handle}}]

        return self.client.search(
            index=self.index,
            body={
                "size": size,
                "_source": ["handle", "bio_raw", "score", "is_ring"],
                "query": query,
            },
        )

    def link_matches(
        self, external_url: str, *, exclude_handle: str | None = None
    ) -> list[str]:
        """Handles already in the index sharing this exact external link.

        Complementary to semantic search rather than redundant with it: a ring
        will reword the bio but keep the same link, so this catches the case where
        the embedding drifts far enough apart to fall out of the kNN results.
        """
        if not external_url.strip():
            return []
        query: dict[str, Any] = {
            "bool": {"must": [{"term": {"external_url": external_url}}]}
        }
        if exclude_handle:
            query["bool"]["must_not"] = [{"term": {"handle": exclude_handle}}]
        response = self.client.search(
            index=self.index,
            body={"size": 10, "_source": ["handle"], "query": query},
        )
        return [h["_source"]["handle"] for h in response.get("hits", {}).get("hits", [])]

    def hybrid_search(
        self,
        text: str,
        *,
        external_url: str = "",
        exclude_handle: str | None = None,
        size: int = 5,
    ) -> dict[str, Any]:
        """Reciprocal rank fusion over semantic + exact-link retrievers.

        Catfish rings reword the bio but reuse the link. Semantic alone misses
        the link; BM25 alone misses the rewrite. RRF is a native retriever, so
        the fusion is one query rather than two round trips and a merge.
        """
        if not text.strip():
            raise ValueError(
                "hybrid_search needs non-empty text: the semantic retriever's "
                "inference endpoint rejects empty input with a 400."
            )
        retrievers: list[dict[str, Any]] = [
            {"standard": {"query": {"semantic": {"field": "bio", "query": text}}}}
        ]
        if external_url:
            retrievers.append(
                {"standard": {"query": {"term": {"external_url": external_url}}}}
            )

        body: dict[str, Any] = {
            "size": size,
            "retriever": {
                "rrf": {
                    "retrievers": retrievers,
                    "rank_window_size": 50,
                    "rank_constant": 20,
                }
            },
        }
        if exclude_handle:
            # Without this, re-scoring an existing handle retrieves its own
            # document as the top match.
            body["retriever"]["rrf"]["filter"] = [
                {"bool": {"must_not": [{"term": {"handle": exclude_handle}}]}}
            ]
        return self.client.search(index=self.index, body=body)

    # ------------------------------------------------------------- aggregations

    def ring_fingerprint(self, *, size: int = 15) -> dict[str, Any]:
        """Discover what flagged bios have in common.

        `significant_text` surfaces terms statistically over-represented in the
        flagged set versus the whole corpus. The word list is never written by
        hand — the aggregation derives it. This has no equivalent in a plain
        relational database, and it is the demo's strongest single moment.
        """
        return self.client.search(
            index=self.index,
            body={
                "size": 0,
                "query": {"term": {"is_ring": True}},
                "aggs": {
                    "fingerprint": {
                        "significant_text": {"field": "bio_raw", "size": size}
                    }
                },
            },
        )

    def creation_histogram(self) -> dict[str, Any]:
        """Are the flagged accounts mass-produced?

        Organic accounts spread across time; farmed ones cluster into a week or
        two. One aggregation, no model call.
        """
        return self.client.search(
            index=self.index,
            body={
                "size": 0,
                "query": {"term": {"is_ring": True}},
                "aggs": {
                    "created": {
                        "date_histogram": {
                            "field": "first_post_at",
                            "calendar_interval": "week",
                        }
                    }
                },
            },
        )

    def gap_by_interest(self) -> dict[str, Any]:
        """Average claimed-vs-evidence gap per interest, across the corpus.

        Backs the Kibana bar chart. Nested aggregation because `claims` is a
        nested field — flattening would cross-contaminate interests.
        """
        return self.client.search(
            index=self.index,
            body={
                "size": 0,
                "aggs": {
                    "claims": {
                        "nested": {"path": "claims"},
                        "aggs": {
                            "by_interest": {
                                "terms": {"field": "claims.interest", "size": 10},
                                "aggs": {
                                    "avg_claimed": {"avg": {"field": "claims.claimed"}},
                                    "avg_evidence": {
                                        "avg": {"field": "claims.evidence"}
                                    },
                                },
                            }
                        },
                    }
                },
            },
        )

    # -------------------------------------------------------------------- write

    def remember(self, document: dict[str, Any]) -> dict[str, Any]:
        """Persist a scored profile so the next one is judged against it.

        The handle is used as the document id, which makes writes idempotent: a
        re-score updates the existing document instead of adding a second copy.
        Without this, checking the same account twice leaves two documents in the
        index, they both come back in recall, and the account matches *itself* as
        a near-duplicate — the exact false positive the ring detection exists to
        avoid. (Verified: @patagonia ended up with two copies before this fix.)
        """
        handle = document.get("handle")
        if not handle:
            raise ValueError("document needs a 'handle' to be addressable")
        return self.client.index(index=self.index, id=handle, document=document)


def similar_summary(hits: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten an ES response into the compact context passed to the model.

    Sending raw hits wastes prompt tokens on `_index`/`_shard` noise; this keeps
    only the fields that inform a judgment.
    """
    return [
        {
            "handle": hit["_source"].get("handle"),
            "bio": hit["_source"].get("bio_raw"),
            "similarity": round(hit.get("_score", 0.0), 3),
            "previously_flagged": bool(hit["_source"].get("is_ring")),
        }
        for hit in hits.get("hits", {}).get("hits", [])
    ]
