"""Apify ingestion edge cases.

The important ones here are the responses that look like success. Apify returns
HTTP 200 with a populated row for a handle that does not exist, so anything that
only checks the status code or the row count will happily index a fabricated
empty profile.
"""

from __future__ import annotations

import httpx
import pytest

from receipts.apify_source import (
    PrivateProfileError,
    ProfileNotFoundError,
    fetch_profile,
)
from receipts.config import Config

CFG = Config(
    apify_token="t",
    es_endpoint="e",
    es_api_key="k",
    anthropic_api_key="a",
    index="receipts",
    model="claude-opus-5",
    effort="medium",
    judge_backend="auto",
    kibana_endpoint="kb",
    elastic_llm_connector="conn",
)


def stub_response(monkeypatch, payload, status: int = 200) -> None:
    def fake_post(*args, **kwargs):
        return httpx.Response(
            status,
            json=payload,
            request=httpx.Request("POST", "https://api.apify.com/x"),
        )

    monkeypatch.setattr(httpx, "post", fake_post)


def test_nonexistent_handle_raises_rather_than_returning_a_blank_profile(
    monkeypatch,
) -> None:
    """Regression: this shape is HTTP 200 with a row, and used to index as real."""
    stub_response(
        monkeypatch,
        [
            {
                "error": "not_found",
                "errorDescription": "Post does not exist",
                "username": "does_not_exist",
                "url": "https://www.instagram.com/does_not_exist/",
            }
        ],
    )

    with pytest.raises(ProfileNotFoundError) as exc:
        fetch_profile("does_not_exist", CFG)

    assert "Post does not exist" in str(exc.value)


def test_error_without_description_still_raises(monkeypatch) -> None:
    stub_response(monkeypatch, [{"error": "rate_limited", "username": "x"}])

    with pytest.raises(ProfileNotFoundError) as exc:
        fetch_profile("x", CFG)

    assert "rate_limited" in str(exc.value)


def test_row_with_no_username_raises(monkeypatch) -> None:
    stub_response(monkeypatch, [{"biography": "orphaned row"}])

    with pytest.raises(ProfileNotFoundError):
        fetch_profile("x", CFG)


def test_empty_result_list_raises(monkeypatch) -> None:
    stub_response(monkeypatch, [])

    with pytest.raises(ProfileNotFoundError):
        fetch_profile("x", CFG)


def test_private_account_is_refused(monkeypatch) -> None:
    stub_response(
        monkeypatch, [{"username": "someone", "private": True, "biography": "hi"}]
    )

    with pytest.raises(PrivateProfileError):
        fetch_profile("someone", CFG)


def test_timeout_is_reported_as_such(monkeypatch) -> None:
    stub_response(monkeypatch, {}, status=408)

    with pytest.raises(TimeoutError) as exc:
        fetch_profile("x", CFG)

    assert "300s" in str(exc.value)


def test_leading_at_sign_is_stripped(monkeypatch) -> None:
    captured: dict = {}

    def fake_post(url, **kwargs):
        captured.update(kwargs.get("json") or {})
        return httpx.Response(
            200,
            json=[{"username": "someone", "biography": "hi"}],
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    fetch_profile("@someone", CFG)

    assert captured["directUrls"] == ["https://www.instagram.com/someone/"]


def test_blank_handle_rejected_before_any_request(monkeypatch) -> None:
    def explode(*args, **kwargs):
        raise AssertionError("should not have issued a request")

    monkeypatch.setattr(httpx, "post", explode)

    with pytest.raises(ValueError):
        fetch_profile("   ", CFG)


def test_valid_profile_parses(monkeypatch) -> None:
    stub_response(
        monkeypatch,
        [
            {
                "username": "real",
                "biography": "a bio",
                "followersCount": 100,
                "latestPosts": [{"caption": "hello", "hashtags": ["x"]}],
            }
        ],
    )

    profile = fetch_profile("real", CFG)

    assert profile.handle == "real"
    assert profile.captions == ["hello"]
    assert profile.hashtags == ["x"]
