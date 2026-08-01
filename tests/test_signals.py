"""Risk signals: the arithmetic half of the verdict.

These are what let the output answer "why that score?" without relying on model
prose, so they need to be right and they need to fire only when they should.
"""

from __future__ import annotations

from receipts.models import Profile, Verdict
from receipts.signals import (
    LOPSIDED_RATIO,
    RING_THRESHOLD,
    THIN_POST_COUNT,
    likelihood,
    risk_signals,
)


def profile(**overrides) -> Profile:
    base = dict(
        handle="x",
        bio="Some bio text here",
        captions=["a", "b", "c"],
        followers=10_000,
        follows=500,
        post_count=400,
        joined_recently=False,
    )
    base.update(overrides)
    return Profile(**base)


def verdict(**overrides) -> Verdict:
    base = dict(
        score=8,
        verdict="Looks consistent.",
        ring_score=0.1,
        confidence="high",
        claims=[
            {"interest": "fitness", "claimed": 5, "evidence": 5, "receipt": "matches"}
        ],
    )
    base.update(overrides)
    return Verdict(**base)


def fired(signals) -> set[str]:
    return {s.name for s in signals if s.fired}


def test_healthy_account_fires_nothing() -> None:
    assert fired(risk_signals(profile(), verdict())) == set()


def test_every_signal_is_always_reported_even_when_clear() -> None:
    """The output should show what was ruled out, not just what tripped."""
    signals = risk_signals(profile(), verdict())

    assert len(signals) == 7
    assert all(s.detail for s in signals)  # every signal explains itself


def test_template_bio_fires_at_threshold() -> None:
    assert "template bio" in fired(
        risk_signals(profile(), verdict(ring_score=RING_THRESHOLD))
    )
    assert "template bio" not in fired(
        risk_signals(profile(), verdict(ring_score=RING_THRESHOLD - 0.01))
    )


def test_unsupported_claim_fires_on_a_wide_gap() -> None:
    wide = verdict(
        claims=[
            {"interest": "travel", "claimed": 9, "evidence": 1, "receipt": "none"}
        ]
    )
    assert "unsupported claim" in fired(risk_signals(profile(), wide))


def test_unsupported_claim_quiet_when_evidence_matches_the_claim() -> None:
    honest = verdict(
        claims=[
            {"interest": "travel", "claimed": 9, "evidence": 8, "receipt": "lots"}
        ]
    )
    assert "unsupported claim" not in fired(risk_signals(profile(), honest))


def test_thin_history_fires_below_threshold() -> None:
    assert "thin history" in fired(
        risk_signals(profile(post_count=THIN_POST_COUNT - 1), verdict())
    )
    assert "thin history" not in fired(
        risk_signals(profile(post_count=THIN_POST_COUNT), verdict())
    )


def test_lopsided_following_detects_farmed_shape() -> None:
    farmed = profile(followers=1_000, follows=int(1_000 * LOPSIDED_RATIO) + 1)
    assert "lopsided following" in fired(risk_signals(farmed, verdict()))


def test_zero_followers_does_not_divide_by_zero() -> None:
    signals = risk_signals(profile(followers=0, follows=5_000), verdict())
    assert "lopsided following" in fired(signals)


def test_empty_bio_and_captions_fire_their_own_signals() -> None:
    bare = profile(bio="", captions=[])
    names = fired(risk_signals(bare, verdict()))

    assert "no bio to check" in names
    assert "no captions" in names


def test_no_claims_does_not_crash() -> None:
    signals = risk_signals(profile(), verdict(claims=[]))
    assert "no interests scored" in [s.detail for s in signals]


def test_likelihood_bands() -> None:
    # A template match dominates everything else.
    band, _ = likelihood(verdict(ring_score=0.9), risk_signals(profile(), verdict()))
    assert band == "LIKELY MASS-PRODUCED"

    healthy = verdict()
    band, _ = likelihood(healthy, risk_signals(profile(), healthy))
    assert band == "CONSISTENT"

    weak = verdict(score=2)
    band, _ = likelihood(weak, risk_signals(profile(), weak))
    assert band == "POORLY SUPPORTED"

    middling = verdict(score=5)
    band, _ = likelihood(middling, risk_signals(profile(), middling))
    assert band == "PARTLY SUPPORTED"


def test_likelihood_never_asserts_the_person_is_fake() -> None:
    """Wording is a claim about evidence, never about the human being."""
    for score in range(0, 11):
        for ring in (0.0, 0.5, 0.95):
            band, because = likelihood(
                verdict(score=score, ring_score=ring),
                risk_signals(profile(), verdict(score=score, ring_score=ring)),
            )
            combined = f"{band} {because}".lower()
            for banned in ("fake", "liar", "lying", "fraud", "catfish"):
                assert banned not in combined, f"{banned!r} in {combined!r}"
