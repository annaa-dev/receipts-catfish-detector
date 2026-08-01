"""Pipeline wiring: recall-before-write ordering, document shape, ring flagging."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from receipts.judge import stub_verdict
from receipts.memory import Memory
from receipts.models import Profile, Verdict
from receipts.pipeline import RING_THRESHOLD, to_document

from .test_memory import RecordingClient

FIXTURE = Path(__file__).parent / "fixtures" / "raw_profile.json"


@pytest.fixture
def profile() -> Profile:
    return Profile.from_apify(json.loads(FIXTURE.read_text())[0])


def verdict(ring_score: float) -> Verdict:
    return Verdict(
        score=4,
        verdict="Two sentences here.",
        ring_score=ring_score,
        confidence="medium",
        claims=[
            {
                "interest": "outdoorsy",
                "claimed": 9,
                "evidence": 2,
                "receipt": "2 of 12 captions",
            }
        ],
    )


def test_document_writes_bio_to_both_fields(profile: Profile) -> None:
    doc = to_document(profile, verdict(0.0), scraped_at="2026-07-31T00:00:00Z")

    # Same text, two mappings — semantic_text for kNN, text for BM25/aggs.
    assert doc["bio"] == doc["bio_raw"] == profile.bio


def test_is_ring_set_at_threshold(profile: Profile) -> None:
    below = to_document(profile, verdict(RING_THRESHOLD - 0.01), scraped_at="t")
    at = to_document(profile, verdict(RING_THRESHOLD), scraped_at="t")

    assert below["is_ring"] is False
    assert at["is_ring"] is True


def test_claims_are_serialized_for_the_nested_field(profile: Profile) -> None:
    doc = to_document(profile, verdict(0.0), scraped_at="t")

    assert doc["claims"] == [
        {
            "interest": "outdoorsy",
            "claimed": 9,
            "evidence": 2,
            "receipt": "2 of 12 captions",
        }
    ]


def test_recall_runs_before_write(profile: Profile, monkeypatch) -> None:
    """A profile must not be able to match itself.

    If remember() ran before the similarity search, re-scoring an existing
    handle would retrieve its own document as a near-perfect match and drive
    ring_score to 1.0 — the pipeline would flag every repeat as a fake.
    """
    from receipts import pipeline

    client = RecordingClient()
    memory = Memory(client, "receipts")
    order: list[str] = []

    original_search = memory.semantic_search
    original_remember = memory.remember

    def tracked_search(*args, **kwargs):
        order.append("recall")
        return original_search(*args, **kwargs)

    def tracked_remember(*args, **kwargs):
        order.append("write")
        return original_remember(*args, **kwargs)

    monkeypatch.setattr(memory, "semantic_search", tracked_search)
    monkeypatch.setattr(memory, "remember", tracked_remember)
    monkeypatch.setattr(pipeline, "fetch_profile", lambda handle, cfg: profile)

    pipeline.score_handle("nasa", cfg=None, memory=memory, dry_run=True)

    assert order == ["recall", "write"]


def test_dry_run_needs_no_model_and_still_indexes(profile: Profile, monkeypatch) -> None:
    from receipts import pipeline

    client = RecordingClient()
    memory = Memory(client, "receipts")
    monkeypatch.setattr(pipeline, "fetch_profile", lambda handle, cfg: profile)

    returned_profile, returned_verdict, similar = pipeline.score_handle(
        "nasa", cfg=None, memory=memory, dry_run=True
    )

    assert returned_profile.handle == "nasa"
    assert 0 <= returned_verdict.score <= 10
    assert similar == []
    assert len(client.indexed) == 1


def test_empty_bio_omits_the_semantic_field(profile: Profile) -> None:
    """Regression: @airbnb has no bio and broke the pipeline with a 400.

    `semantic_text` forwards the value to an inference endpoint at index time,
    and that endpoint rejects empty input. Omitting the key is the fix; writing
    "" is not.
    """
    blank = profile.model_copy(update={"bio": ""})
    doc = to_document(blank, verdict(0.0), scraped_at="t")

    assert "bio" not in doc
    assert doc["bio_raw"] == ""  # text field keeps a consistent schema


def test_whitespace_only_bio_treated_as_empty(profile: Profile) -> None:
    blank = profile.model_copy(update={"bio": "   \n  "})
    doc = to_document(blank, verdict(0.0), scraped_at="t")

    assert "bio" not in doc


def test_empty_bio_skips_semantic_recall(profile: Profile, monkeypatch) -> None:
    """No bio means nothing to match on — and no semantic call to fail on.

    The exact-link check may still run, since a shared link is evidence even when
    the bio is blank. What must not happen is a semantic query on empty text,
    which is a 400 from the inference endpoint.
    """
    from receipts import pipeline

    client = RecordingClient()
    memory = Memory(client, "receipts")
    blank = profile.model_copy(update={"bio": "", "external_url": ""})
    monkeypatch.setattr(pipeline, "fetch_profile", lambda handle, cfg: blank)

    _, _, similar = pipeline.score_handle("x", cfg=None, memory=memory, dry_run=True)

    assert similar == []
    assert client.searches == []       # no bio and no link: nothing to query
    assert len(client.indexed) == 1    # still indexed


def test_empty_bio_still_checks_for_a_shared_link(
    profile: Profile, monkeypatch
) -> None:
    from receipts import pipeline

    client = RecordingClient({"hits": {"hits": [{"_source": {"handle": "twin"}}]}})
    memory = Memory(client, "receipts")
    blank = profile.model_copy(
        update={"bio": "", "external_url": "https://linktr.ee/x"}
    )
    monkeypatch.setattr(pipeline, "fetch_profile", lambda handle, cfg: blank)

    _, _, similar = pipeline.score_handle("x", cfg=None, memory=memory, dry_run=True)

    # One query — the link check — and no semantic query on empty text.
    assert len(client.searches) == 1
    assert "semantic" not in json.dumps(client.searches[0]["body"])
    assert similar == [
        {
            "handle": "twin",
            "bio": "",
            "similarity": None,
            "previously_flagged": False,
            "same_link": True,
        }
    ]


def test_recall_excludes_the_handle_being_scored(
    profile: Profile, monkeypatch
) -> None:
    """Regression: @patagonia matched itself after being re-scored."""
    from receipts import pipeline

    client = RecordingClient()
    memory = Memory(client, "receipts")
    monkeypatch.setattr(pipeline, "fetch_profile", lambda handle, cfg: profile)

    pipeline.score_handle("nasa", cfg=None, memory=memory, dry_run=True)

    semantic_query = client.searches[0]["body"]["query"]["bool"]
    assert semantic_query["must_not"] == [{"term": {"handle": "nasa"}}]


def test_stub_verdict_satisfies_the_schema(profile: Profile) -> None:
    """The offline path must produce the same shape as the model path."""
    result = stub_verdict(profile)

    assert isinstance(result, Verdict)
    assert len(result.claims) == 6  # one per tracked interest
    assert all(0 <= c.claimed <= 10 and 0 <= c.evidence <= 10 for c in result.claims)
