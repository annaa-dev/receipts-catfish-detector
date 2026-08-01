"""Profile normalization, tested against a real Apify response.

The fixture is an actual `apify/instagram-scraper` run against a public
institutional account. Using real output rather than a hand-written dict is the
point: it locks in the field names the actor genuinely returns.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from receipts.models import Profile, Verdict

FIXTURE = Path(__file__).parent / "fixtures" / "raw_profile.json"


@pytest.fixture
def raw() -> dict:
    return json.loads(FIXTURE.read_text())[0]


def test_maps_real_apify_output(raw: dict) -> None:
    profile = Profile.from_apify(raw)

    assert profile.handle == "nasa"
    assert profile.bio  # non-empty
    assert profile.is_private is False
    assert profile.followers > 0
    assert profile.post_count > 0


def test_extracts_captions_from_latest_posts(raw: dict) -> None:
    profile = Profile.from_apify(raw)

    # The actor returns captions nested under latestPosts in the same response
    # as the profile, so one call yields both bio and content.
    assert len(profile.captions) > 0
    assert all(isinstance(c, str) and c.strip() for c in profile.captions)


def test_tolerates_missing_optional_fields() -> None:
    profile = Profile.from_apify({"username": "someone"})

    assert profile.handle == "someone"
    assert profile.bio == ""
    assert profile.captions == []
    assert profile.followers == 0


def test_drops_empty_captions() -> None:
    profile = Profile.from_apify(
        {
            "username": "x",
            "latestPosts": [
                {"caption": "real caption"},
                {"caption": ""},
                {"caption": None},
                {},
            ],
        }
    )

    assert profile.captions == ["real caption"]


def test_claim_gap_is_claimed_minus_evidence() -> None:
    verdict = Verdict(
        score=3,
        verdict="Two sentences.",
        ring_score=0.1,
        confidence="medium",
        claims=[
            {
                "interest": "outdoorsy",
                "claimed": 9,
                "evidence": 1,
                "receipt": "0 of 12 captions mention trails",
            }
        ],
    )

    assert verdict.claims[0].gap == 8


def test_score_bounds_are_enforced() -> None:
    with pytest.raises(Exception):
        Verdict(
            score=99,  # out of range — schema must reject
            verdict="x",
            ring_score=0.0,
            confidence="low",
            claims=[],
        )
