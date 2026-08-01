"""Apify ingestion.

Why Apify rather than an official API: Instagram's Graph API only exposes
accounts you own. There is no endpoint for reading someone else's public
profile, so scraping is the only path to this data — and anti-bot handling,
pagination, and proxy rotation are undifferentiated work worth outsourcing.
"""

from __future__ import annotations

import httpx

from .config import APIFY_SYNC_URL, APIFY_TIMEOUT_S, Config
from .models import Profile


class PrivateProfileError(RuntimeError):
    """Raised when a handle resolves to a private account.

    Public accounts only — a private profile is skipped rather than partially
    scraped.
    """


class ProfileNotFoundError(RuntimeError):
    """Raised when the actor returns no rows for a handle."""


def fetch_profile(handle: str, cfg: Config, *, results_limit: int = 1) -> Profile:
    """Scrape one public profile and return it normalized.

    Uses run-sync-get-dataset-items so the actor runs and returns rows in a
    single request — no polling loop, no dataset bookkeeping.
    """
    handle = handle.lstrip("@").strip()
    if not handle:
        raise ValueError("handle is empty")

    payload = {
        "directUrls": [f"https://www.instagram.com/{handle}/"],
        "resultsType": "details",
        "resultsLimit": results_limit,
        "addParentData": False,
    }
    response = httpx.post(
        APIFY_SYNC_URL,
        json=payload,
        headers={"Authorization": f"Bearer {cfg.apify_token}"},
        timeout=APIFY_TIMEOUT_S,
    )
    if response.status_code == 408:
        raise TimeoutError(
            "Apify hit its 300s sync cap. Lower results_limit or use the async "
            "run + dataset endpoints."
        )
    response.raise_for_status()

    rows = response.json()
    if not rows:
        raise ProfileNotFoundError(f"No data returned for @{handle}")

    # A nonexistent handle does NOT come back as an empty list or an HTTP error.
    # The actor returns one row carrying an `error` key:
    #
    #   {"error": "not_found", "errorDescription": "Post does not exist",
    #    "username": "...", "url": "..."}
    #
    # Without this check that row parses into a Profile with an empty bio, zero
    # followers, and no captions — and the pipeline scores and indexes it as if
    # it were real. Verified the hard way: it overwrote a seeded record. A
    # mistyped handle on stage must fail loudly, not produce a confident verdict
    # about an account that does not exist.
    row = rows[0]
    if row.get("error"):
        raise ProfileNotFoundError(
            f"@{handle}: {row.get('errorDescription') or row['error']}"
        )

    profile = Profile.from_apify(row)
    if not profile.handle:
        raise ProfileNotFoundError(f"@{handle}: response carried no username")
    if profile.is_private:
        raise PrivateProfileError(
            f"@{handle} is private. This tool reads public accounts only."
        )
    return profile
