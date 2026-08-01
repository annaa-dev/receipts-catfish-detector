"""Environment configuration with explicit validation.

Fails loudly at startup rather than deep inside a pipeline run — at a hackathon,
a clear "ES_API_KEY is not set" beats a stack trace at minute 40.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()

# Apify's official Instagram scraper. Verified live: returns one object per
# profile with `biography`, `latestPosts` (12 posts incl. captions), `private`,
# `joinedRecently`, `verified`, `externalUrl`, `followersCount`, `postsCount`.
APIFY_ACTOR = "apify~instagram-scraper"

# run-sync-get-dataset-items runs the actor and returns rows in one HTTP call.
# Hard 300s server-side cap -> 408 if exceeded.
APIFY_SYNC_URL = f"https://api.apify.com/v2/acts/{APIFY_ACTOR}/run-sync-get-dataset-items"
APIFY_TIMEOUT_S = 240

# The six interests we score. Fixed on purpose: letting the model invent a
# taxonomy per profile makes cross-profile aggregation meaningless.
INTERESTS = ("outdoorsy", "fitness", "foodie", "travel", "nightlife", "arts")


class ConfigError(RuntimeError):
    """Raised when required environment configuration is missing."""


@dataclass(frozen=True)
class Config:
    apify_token: str
    es_endpoint: str
    es_api_key: str
    anthropic_api_key: str
    index: str
    model: str
    effort: str

    @classmethod
    def load(cls, *, require: tuple[str, ...] = ()) -> "Config":
        """Load config from the environment.

        `require` names the logical subsystems this command actually needs
        ("apify", "es", "claude"), so `receipts search` doesn't demand an
        Anthropic key it will never use.
        """
        cfg = cls(
            apify_token=os.getenv("APIFY_TOKEN", ""),
            es_endpoint=os.getenv("ES_ENDPOINT", ""),
            es_api_key=os.getenv("ES_API_KEY", ""),
            anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", ""),
            index=os.getenv("RECEIPTS_INDEX", "receipts"),
            model=os.getenv("CLAUDE_MODEL", "claude-opus-5"),
            effort=os.getenv("CLAUDE_EFFORT", "medium"),
        )
        cfg.validate(require)
        return cfg

    def validate(self, require: tuple[str, ...]) -> None:
        needed: dict[str, list[tuple[str, str]]] = {
            "apify": [("APIFY_TOKEN", self.apify_token)],
            "es": [("ES_ENDPOINT", self.es_endpoint), ("ES_API_KEY", self.es_api_key)],
            "claude": [("ANTHROPIC_API_KEY", self.anthropic_api_key)],
        }
        missing = [
            name
            for subsystem in require
            for name, value in needed.get(subsystem, [])
            if not value
        ]
        if missing:
            raise ConfigError(
                "Missing required environment variables: "
                + ", ".join(missing)
                + ". Copy .env.example to .env and fill them in."
            )
