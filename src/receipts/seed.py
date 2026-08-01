"""Synthetic ring seed data.

Why this exists: `significant_text` and the creation-date histogram both need a
*flagged cohort* to contrast against the rest of the corpus. Real public accounts
scraped from Instagram are not a catfish ring, and flagging them as one would put
a fabricated claim into the index where a later query would read it back as fact.

So the flagged cohort is generated instead, and every document it produces is
stamped `synthetic: true`. That makes it filterable, auditable, and impossible to
mistake for scraped data:

    GET receipts/_search { "query": { "term": { "synthetic": true } } }

These bios are constructed the way mass-produced ones actually are — a shared
vocabulary pool recombined with varied syntax. That matters for both queries:
if the rewording were total there would be no over-represented terms for
`significant_text` to find, and if the wording were identical then plain BM25
would catch it and the semantic layer would be pointless.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

# One template, twelve rewordings. Same claimed interests, same structure, same
# vocabulary pool — different words on the surface.
RING_BIOS: tuple[str, ...] = (
    "Entrepreneur | Crypto investor | Coffee, gym, travel | God first",
    "Founder & digital asset trader. Espresso, fitness, seeing the world. Faith first.",
    "Business owner 📈 Crypto & stocks | Lifting, lattes, flights | Blessed",
    "Serial entrepreneur. Web3 investor. Gym rat, coffee addict, always travelling.",
    "CEO 💼 | Digital currency trading | Fitness + caffeine + passport stamps | Faith over fear",
    "Building businesses. Trading crypto. Coffee before the gym, boarding pass after.",
    "Investor & founder | Blockchain | Strong coffee, heavy weights, one-way tickets | God is good",
    "Self made 📊 Crypto portfolio | Espresso, iron, airports | Faith first always",
    "Entrepreneur and trader. Fitness is discipline. Coffee is fuel. Travel is education.",
    "Founder | Digital assets | Barbells & americanos | Passport always ready | Blessed daily",
    "Own my time 💰 Crypto investing | Gym, coffee, next flight | Grateful, faith led",
    "Startup founder. Crypto since 2017. Lifting heavy, drinking espresso, chasing sunsets.",
)

# Farmed accounts cluster in time. These are spread across five weeks from a
# fixed base date so the histogram is deterministic across runs.
_BASE = date(2026, 5, 4)
_DAY_OFFSETS = (0, 2, 3, 6, 9, 11, 14, 17, 21, 26, 30, 33)


def ring_documents() -> list[dict[str, Any]]:
    """Build the flagged synthetic cohort.

    `is_ring` is True here as a statement of fact about generated data: these
    twelve documents genuinely are one template recombined, because that is how
    they were constructed.
    """
    documents: list[dict[str, Any]] = []
    for index, (bio, offset) in enumerate(zip(RING_BIOS, _DAY_OFFSETS)):
        created = _BASE + timedelta(days=offset)
        documents.append(
            {
                "handle": f"synthetic_ring_{index + 1:02d}",
                "bio": bio,
                "bio_raw": bio,
                "external_url": "https://example.invalid/synthetic",
                "followers": 900 + index * 137,
                "follows": 4000 + index * 90,  # farmed ratio: follows >> followers
                "post_count": 6 + index,
                "verified": False,
                "joined_recently": True,
                "first_post_at": created.isoformat(),
                "scraped_at": created.isoformat(),
                "score": 2,
                "verdict": (
                    "Synthetic demo record. Bio is one template recombined; "
                    "claimed interests are identical across the cohort."
                ),
                "ring_score": 0.92,
                "is_ring": True,
                "confidence": "high",
                "synthetic": True,
                "claims": [
                    {
                        "interest": "fitness",
                        "claimed": 8,
                        "evidence": 1,
                        "receipt": "no captioned posts to support it",
                    },
                    {
                        "interest": "travel",
                        "claimed": 8,
                        "evidence": 1,
                        "receipt": "no captioned posts to support it",
                    },
                    {
                        "interest": "foodie",
                        "claimed": 6,
                        "evidence": 1,
                        "receipt": "coffee named in bio, absent from posts",
                    },
                ],
            }
        )
    return documents
