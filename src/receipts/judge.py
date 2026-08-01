"""Claude scores claimed-vs-actual identity.

Why a model at all: the scoring needs judgment *with a citation*. It has to say
why, quote a real caption, and refuse to invent a receipt when the evidence is
thin. Structured outputs constrain the response to `Verdict`, so the score lands
in a typed field instead of being regex'd out of prose.
"""

from __future__ import annotations

from typing import Any

import anthropic

from .config import INTERESTS, Config
from .models import Claim, Profile, Verdict

SYSTEM = """You are a blunt, funny catfish auditor.

Rules you never break:
- Cite only evidence present in the data you are given.
- Never invent a receipt, a quote, or a count. If the evidence is thin, say so
  and set confidence to "low".
- A low score means "unverified or inconsistent". It never means "proven fake".
- Never speculate about anyone's gender, sexuality, race, or relationship status.
- You judge only what this person published about themselves. You do not profile
  the people they follow."""

PROMPT = """PROFILE
handle: @{handle}
bio: {bio}
followers: {followers} | follows: {follows} | posts: {post_count}
verified: {verified} | account created recently: {joined_recently}
external link: {external_url}

CAPTIONS FROM RECENT POSTS
{captions}

HASHTAGS USED
{hashtags}

SIMILAR ACCOUNTS ALREADY IN THE INDEX
These came from a semantic search over every profile scored so far. High
similarity with *different wording* is the signal that matters — a mass-produced
bio reworded, not a coincidental phrase match.
{similar}

TASK
For each of these interests: {interests}
  claimed  = 0-10, how strongly the BIO asserts it
  evidence = 0-10, how strongly the CAPTIONS actually prove it
  receipt  = one short factual line, quoting or counting real posts

Then:
  ring_score = 0-1. High only when the wording differs but the meaning, the
               claimed interests, and the structure line up with the similar
               accounts above. An empty similar-accounts list means 0.
  score      = 0-10 overall authenticity. Penalise a high ring_score heavily.
               Also penalise: very few posts, a brand-new account, or a
               follower/follows ratio typical of a farmed account.
  confidence = how much evidence backed this call.
  verdict    = two sentences. Blunt, specific, no hedging filler."""


def _format_captions(captions: list[str], limit: int = 30) -> str:
    if not captions:
        return "(none — this account has no captioned posts)"
    return "\n".join(f"- {c[:400]}" for c in captions[:limit])


def judge(
    profile: Profile,
    similar: list[dict[str, Any]],
    cfg: Config,
    *,
    client: anthropic.Anthropic | None = None,
) -> Verdict:
    """Score one profile against the memory context retrieved for it."""
    client = client or anthropic.Anthropic(api_key=cfg.anthropic_api_key)

    prompt = PROMPT.format(
        handle=profile.handle,
        bio=profile.bio or "(empty)",
        followers=profile.followers,
        follows=profile.follows,
        post_count=profile.post_count,
        verified=profile.verified,
        joined_recently=profile.joined_recently,
        external_url=profile.external_url or "(none)",
        captions=_format_captions(profile.captions),
        hashtags=", ".join(sorted(set(profile.hashtags))[:40]) or "(none)",
        similar=similar or "(none — this is the first profile in the index)",
        interests=", ".join(INTERESTS),
    )

    # `messages.parse` validates the response against the Pydantic model and
    # returns it typed, so a malformed score is an SDK-level error rather than a
    # downstream KeyError.
    #
    # Deliberately NOT passed: `temperature`. Sampling parameters were removed
    # on Claude Opus 5 and return a 400. Determinism comes from the prompt.
    #
    # `max_tokens` covers thinking *and* response text — thinking is on by
    # default on Opus 5, so this is sized well above the JSON payload.
    response = client.messages.parse(
        model=cfg.model,
        max_tokens=8000,
        system=SYSTEM,
        messages=[{"role": "user", "content": prompt}],
        output_format=Verdict,
    )

    if response.stop_reason == "refusal":
        raise RuntimeError(
            "Model declined this request. "
            f"Category: {getattr(response.stop_details, 'category', None)}"
        )

    verdict = response.parsed_output
    if verdict is None:
        raise RuntimeError(
            f"Structured output failed to parse (stop_reason={response.stop_reason})."
        )
    return verdict


def stub_verdict(profile: Profile) -> Verdict:
    """Deterministic offline verdict for --dry-run and tests.

    Lets the whole pipeline and CLI be exercised without an Anthropic key, which
    keeps the repo testable in CI and demoable if the connector fails.
    """
    bio = profile.bio.lower()
    claims = [
        Claim(
            interest=interest,
            claimed=8 if interest in bio else 2,
            evidence=min(
                10,
                sum(interest in (c or "").lower() for c in profile.captions) * 3,
            ),
            receipt=(
                f"{sum(interest in (c or '').lower() for c in profile.captions)}"
                f" of {len(profile.captions)} captions mention {interest}"
            ),
        )
        for interest in INTERESTS
    ]
    worst = max(claims, key=lambda c: c.gap)
    return Verdict(
        score=5,
        verdict=(
            f"Offline stub verdict for @{profile.handle}. "
            f"Largest claimed-vs-evidence gap is {worst.interest}."
        ),
        ring_score=0.0,
        confidence="low",
        claims=claims,
    )
