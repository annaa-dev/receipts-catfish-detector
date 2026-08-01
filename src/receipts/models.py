"""Typed schemas.

`Verdict` is handed to the Anthropic SDK as a structured-output format, so the
model is constrained to this shape rather than asked politely for JSON.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Claim(BaseModel):
    """One interest, scored two ways."""

    interest: str = Field(description="One of the six tracked interests.")
    claimed: int = Field(ge=0, le=10, description="How strongly the BIO asserts this.")
    evidence: int = Field(ge=0, le=10, description="How strongly the CAPTIONS prove it.")
    receipt: str = Field(
        description="One short factual line quoting or counting real posts. "
        "Never invented."
    )

    @property
    def gap(self) -> int:
        """Positive means claimed more than the posts support."""
        return self.claimed - self.evidence


class Verdict(BaseModel):
    """The model's full judgment on one profile."""

    score: int = Field(ge=0, le=10, description="Overall authenticity.")
    verdict: str = Field(description="Two blunt, specific sentences.")
    ring_score: float = Field(
        ge=0.0,
        le=1.0,
        description="Likelihood this bio is a reworded copy of the similar "
        "accounts supplied as context.",
    )
    confidence: Literal["low", "medium", "high"] = Field(
        description="How much evidence backed this call. 'low' means thin data."
    )
    claims: list[Claim]


class Profile(BaseModel):
    """Normalized subset of the Apify payload we actually use."""

    handle: str
    bio: str
    captions: list[str]
    hashtags: list[str] = Field(default_factory=list)
    external_url: str = ""
    followers: int = 0
    follows: int = 0
    post_count: int = 0
    verified: bool = False
    joined_recently: bool = False
    is_private: bool = False

    @classmethod
    def from_apify(cls, raw: dict) -> "Profile":
        """Map Apify's instagram-scraper output onto our shape.

        Field names verified against a live actor run; `latestPosts` carries the
        captions and is present in the same response as the profile itself.
        """
        posts = raw.get("latestPosts") or []
        captions = [p.get("caption") or "" for p in posts]
        hashtags: list[str] = []
        for post in posts:
            hashtags.extend(post.get("hashtags") or [])
        return cls(
            handle=raw.get("username") or "",
            bio=raw.get("biography") or "",
            captions=[c for c in captions if c.strip()],
            hashtags=hashtags,
            external_url=raw.get("externalUrl") or "",
            followers=raw.get("followersCount") or 0,
            follows=raw.get("followsCount") or 0,
            post_count=raw.get("postsCount") or 0,
            verified=bool(raw.get("verified")),
            joined_recently=bool(raw.get("joinedRecently")),
            is_private=bool(raw.get("private")),
        )
