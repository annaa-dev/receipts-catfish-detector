"""Objective risk signals, computed from the scrape — no model involved.

These exist so the output can answer "why that score?" with facts rather than
model prose. Every signal here is a plain check against data Apify returned, so
it is reproducible, explainable, and cannot be hallucinated.

The model's `ring_score` and the claimed-vs-evidence gaps are judgment. These are
arithmetic. Showing both is what makes the verdict defensible on stage: when a
judge asks "why does it think that?", you point at a list of checks and say what
each one found.
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import Profile, Verdict

# A bio in the same shape as many others already seen. Above this the account is
# treated as part of a template cohort.
RING_THRESHOLD = 0.7

# Accounts that follow far more people than follow them back are characteristic
# of bulk-created accounts working through follow lists.
LOPSIDED_RATIO = 2.0

# Below this there is very little published material to check a bio against.
THIN_POST_COUNT = 10

# A claim this far above its evidence is the headline contradiction.
NOTABLE_GAP = 5


@dataclass(frozen=True)
class Signal:
    name: str
    fired: bool
    detail: str


def risk_signals(profile: Profile, verdict: Verdict) -> list[Signal]:
    """Return every check, fired or not, so the output shows what was ruled out."""
    follows_ratio = (
        profile.follows / profile.followers if profile.followers else float("inf")
    )
    worst = max(verdict.claims, key=lambda c: c.gap, default=None)

    return [
        Signal(
            "template bio",
            verdict.ring_score >= RING_THRESHOLD,
            f"bio similarity to known template cohort: {verdict.ring_score:.2f}"
            + (" — above threshold" if verdict.ring_score >= RING_THRESHOLD
               else " — below threshold, reads as original"),
        ),
        Signal(
            "unsupported claim",
            bool(worst and worst.gap >= NOTABLE_GAP),
            (
                f"largest gap is {worst.interest}: says {worst.claimed}/10, "
                f"posts show {worst.evidence}/10"
            )
            if worst
            else "no interests scored",
        ),
        Signal(
            "new account",
            profile.joined_recently,
            "Instagram flags this account as recently created"
            if profile.joined_recently
            else "not flagged as recently created",
        ),
        Signal(
            "thin history",
            profile.post_count < THIN_POST_COUNT,
            f"{profile.post_count} posts total — little to verify a bio against"
            if profile.post_count < THIN_POST_COUNT
            else f"{profile.post_count:,} posts — plenty of material to check",
        ),
        Signal(
            "lopsided following",
            follows_ratio > LOPSIDED_RATIO,
            f"follows {profile.follows:,} but only {profile.followers:,} follow back"
            if follows_ratio > LOPSIDED_RATIO
            else f"follows {profile.follows:,} / {profile.followers:,} followers — normal shape",
        ),
        Signal(
            "no bio to check",
            not profile.bio.strip(),
            "bio is empty, so there is no claim to verify"
            if not profile.bio.strip()
            else f"bio present ({len(profile.bio)} characters)",
        ),
        Signal(
            "no captions",
            not profile.captions,
            "no captioned posts, so there is no evidence either way"
            if not profile.captions
            else f"{len(profile.captions)} captions available as evidence",
        ),
    ]


def likelihood(verdict: Verdict, signals: list[Signal]) -> tuple[str, str]:
    """Turn the score and signals into a plain-language likelihood band.

    Deliberately worded as a claim about *evidence*, never about the person.
    "Unverified" is a statement we can defend; "fake" is not.
    """
    fired = sum(1 for s in signals if s.fired)

    if verdict.ring_score >= RING_THRESHOLD:
        return (
            "LIKELY MASS-PRODUCED",
            "this bio matches a template shared with other accounts",
        )
    if verdict.score <= 3 or fired >= 3:
        return (
            "POORLY SUPPORTED",
            f"{fired} risk checks fired and the posts don't back the bio",
        )
    if verdict.score <= 6:
        return (
            "PARTLY SUPPORTED",
            "some claims check out, others have no evidence behind them",
        )
    return ("CONSISTENT", "the bio and the posts broadly agree")
