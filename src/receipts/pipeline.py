"""scrape -> recall -> judge -> remember.

Ordering matters: recall runs *before* the new document is written, so a
re-scored profile cannot match itself and inflate its own ring score.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .apify_source import fetch_profile
from .config import Config
from .judge import judge, stub_verdict
from .memory import Memory, similar_summary
from .models import Profile, Verdict

RING_THRESHOLD = 0.7


def to_document(profile: Profile, verdict: Verdict, *, scraped_at: str) -> dict[str, Any]:
    """Build the indexed document.

    `bio` and `bio_raw` hold the same text on purpose: `bio` is `semantic_text`
    (vector search) and `bio_raw` is analyzed `text` (BM25 and the
    significant_text aggregation, which needs an analyzed field). Writing both
    explicitly avoids depending on `copy_to`, which is one more thing to debug
    under time pressure.
    """
    document = {
        "handle": profile.handle,
        "bio_raw": profile.bio,
        "external_url": profile.external_url,
        "followers": profile.followers,
        "follows": profile.follows,
        "post_count": profile.post_count,
        "verified": profile.verified,
        "joined_recently": profile.joined_recently,
        "scraped_at": scraped_at,
        "score": verdict.score,
        "verdict": verdict.verdict,
        "ring_score": verdict.ring_score,
        "is_ring": verdict.ring_score >= RING_THRESHOLD,
        "confidence": verdict.confidence,
        "claims": [c.model_dump() for c in verdict.claims],
    }

    # `semantic_text` sends the field value to an inference endpoint at index
    # time, and that endpoint rejects empty input with a 400. Plenty of real
    # accounts have no bio at all (verified: @airbnb), so the field is omitted
    # rather than written blank. `bio_raw` still gets "" — a `text` field is
    # happy with it, and keeping the key present means BM25 and the
    # significant_text aggregation see a consistent schema across documents.
    if profile.bio.strip():
        document["bio"] = profile.bio

    return document


def score_handle(
    handle: str,
    cfg: Config,
    memory: Memory,
    *,
    dry_run: bool = False,
) -> tuple[Profile, Verdict, list[dict[str, Any]]]:
    """Run the full loop for one handle. Returns what the CLI needs to render."""
    profile = fetch_profile(handle, cfg)

    # Recall: what has this index seen that means the same thing?
    #
    # An empty bio has nothing to match on, and sending it to the semantic query
    # would fail the same way indexing does. No bio also means the account cannot
    # be a ring member — a reused template is the thing being detected, and there
    # is no template here.
    if profile.bio.strip():
        hits = memory.hybrid_search(profile.bio, external_url=profile.external_url)
        similar = similar_summary(hits)
    else:
        similar = []

    verdict = stub_verdict(profile) if dry_run else judge(profile, similar, cfg)

    memory.remember(
        to_document(
            profile,
            verdict,
            scraped_at=datetime.now(timezone.utc).isoformat(),
        )
    )
    return profile, verdict, similar
