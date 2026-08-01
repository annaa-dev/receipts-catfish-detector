"""Judging via Elastic's managed LLM.

The important thing under test is the JSON handling. This route has no
structured-output enforcement, so the model can wrap its reply in fences, add a
sentence, or return something that fails validation — and each of those has to be
handled rather than crashing a live demo.
"""

from __future__ import annotations

import httpx
import pytest

from receipts.config import Config, kibana_from_es
from receipts.elastic_judge import (
    ElasticJudgeError,
    _extract_json,
    judge_via_elastic,
)
from receipts.models import Profile

CFG = Config(
    apify_token="t",
    es_endpoint="https://proj.es.us-central1.gcp.elastic.cloud:443",
    es_api_key="k",
    anthropic_api_key="",
    index="receipts",
    model="claude-opus-5",
    effort="medium",
    judge_backend="auto",
    kibana_endpoint="https://proj.kb.us-central1.gcp.elastic.cloud",
    elastic_llm_connector=".anthropic-claude-5-sonnet-chat_completion",
)

PROFILE = Profile(
    handle="someone",
    bio="Entrepreneur | Crypto | Coffee, gym, travel",
    captions=["back at it", "new pair"],
    followers=1200,
    follows=4400,
    post_count=7,
)

VALID = {
    "score": 3,
    "verdict": "Bio claims a lot. Posts show almost none of it.",
    "ring_score": 0.88,
    "confidence": "high",
    "claims": [
        {
            "interest": "fitness",
            "claimed": 8,
            "evidence": 1,
            "receipt": "one gym reference in 7 posts",
        }
    ],
}


def connector_reply(content: str, status: int = 200, ok: bool = True):
    body = {
        "status": "ok" if ok else "error",
        "data": {"choices": [{"message": {"content": content, "role": "assistant"}}]},
    }
    return httpx.Response(
        status, json=body, request=httpx.Request("POST", "https://x/y")
    )


# ---------------------------------------------------------------- URL derivation


def test_kibana_url_derived_from_elasticsearch_endpoint() -> None:
    assert (
        kibana_from_es("https://proj.es.us-central1.gcp.elastic.cloud:443")
        == "https://proj.kb.us-central1.gcp.elastic.cloud"
    )


def test_backend_defaults_to_elastic_without_an_anthropic_key() -> None:
    assert CFG.resolved_backend == "elastic"


def test_backend_prefers_anthropic_when_a_key_is_present() -> None:
    from dataclasses import replace

    assert replace(CFG, anthropic_api_key="sk-ant-x").resolved_backend == "anthropic"


def test_explicit_backend_overrides_the_default() -> None:
    from dataclasses import replace

    assert replace(CFG, judge_backend="elastic").resolved_backend == "elastic"


# ------------------------------------------------------------------ JSON parsing


def test_extracts_bare_json() -> None:
    assert _extract_json('{"a": 1}') == {"a": 1}


def test_extracts_json_from_markdown_fences() -> None:
    """The prompt forbids fences, but nothing enforces that."""
    assert _extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert _extract_json('```\n{"a": 1}\n```') == {"a": 1}


def test_extracts_json_with_surrounding_prose() -> None:
    assert _extract_json('Sure! Here you go: {"a": 1} Hope that helps.') == {"a": 1}


def test_raises_when_there_is_no_json_at_all() -> None:
    with pytest.raises(ElasticJudgeError) as exc:
        _extract_json("I'd rather not.")
    assert "no JSON object" in str(exc.value)


# -------------------------------------------------------------------- happy path


def test_valid_reply_parses_into_a_verdict(monkeypatch) -> None:
    import json as _json

    monkeypatch.setattr(
        httpx, "post", lambda *a, **k: connector_reply(_json.dumps(VALID))
    )
    verdict = judge_via_elastic(PROFILE, [], CFG)

    assert verdict.score == 3
    assert verdict.ring_score == 0.88
    assert verdict.claims[0].gap == 7


def test_request_targets_the_kibana_connector_execute_endpoint(monkeypatch) -> None:
    """The Elasticsearch _inference endpoints 403 for an API-key caller."""
    import json as _json

    seen: dict = {}

    def capture(url, **kwargs):
        seen["url"] = url
        seen["headers"] = kwargs.get("headers", {})
        seen["json"] = kwargs.get("json", {})
        return connector_reply(_json.dumps(VALID))

    monkeypatch.setattr(httpx, "post", capture)
    judge_via_elastic(PROFILE, [], CFG)

    assert seen["url"] == (
        "https://proj.kb.us-central1.gcp.elastic.cloud/api/actions/connector/"
        ".anthropic-claude-5-sonnet-chat_completion/_execute"
    )
    assert seen["headers"]["Authorization"] == "ApiKey k"
    assert seen["headers"]["kbn-xsrf"] == "true"
    assert seen["json"]["params"]["subAction"] == "unified_completion"


# ------------------------------------------------------------------- repair path


def test_invalid_first_reply_triggers_one_repair_attempt(monkeypatch) -> None:
    import json as _json

    calls: list[list[dict]] = []

    def two_replies(url, **kwargs):
        calls.append(kwargs["json"]["params"]["subActionParams"]["body"]["messages"])
        if len(calls) == 1:
            return connector_reply('{"score": 99, "verdict": "x"}')  # invalid
        return connector_reply(_json.dumps(VALID))

    monkeypatch.setattr(httpx, "post", two_replies)
    verdict = judge_via_elastic(PROFILE, [], CFG)

    assert verdict.score == 3
    assert len(calls) == 2
    # The repair turn must show the model its own bad output plus the error.
    assert calls[1][-2]["role"] == "assistant"
    assert "failed validation" in calls[1][-1]["content"]


def test_gives_up_after_two_failures_rather_than_looping(monkeypatch) -> None:
    monkeypatch.setattr(
        httpx, "post", lambda *a, **k: connector_reply('{"nope": true}')
    )

    with pytest.raises(ElasticJudgeError) as exc:
        judge_via_elastic(PROFILE, [], CFG)

    assert "twice" in str(exc.value)


# ---------------------------------------------------------------- error surfaces


def test_non_200_is_reported_with_the_body(monkeypatch) -> None:
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *a, **k: httpx.Response(
            403,
            text="Forbidden",
            request=httpx.Request("POST", "https://x/y"),
        ),
    )

    with pytest.raises(ElasticJudgeError) as exc:
        judge_via_elastic(PROFILE, [], CFG)

    assert "403" in str(exc.value)


def test_connector_level_failure_is_surfaced(monkeypatch) -> None:
    monkeypatch.setattr(
        httpx, "post", lambda *a, **k: connector_reply("{}", ok=False)
    )

    with pytest.raises(ElasticJudgeError) as exc:
        judge_via_elastic(PROFILE, [], CFG)

    assert "reported failure" in str(exc.value)


def test_network_error_names_the_url(monkeypatch) -> None:
    def boom(*a, **k):
        raise httpx.ConnectError("no route")

    monkeypatch.setattr(httpx, "post", boom)

    with pytest.raises(ElasticJudgeError) as exc:
        judge_via_elastic(PROFILE, [], CFG)

    assert "could not reach Kibana" in str(exc.value)


def test_unexpected_response_shape_is_surfaced(monkeypatch) -> None:
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *a, **k: httpx.Response(
            200,
            json={"status": "ok", "data": {}},
            request=httpx.Request("POST", "https://x/y"),
        ),
    )

    with pytest.raises(ElasticJudgeError) as exc:
        judge_via_elastic(PROFILE, [], CFG)

    assert "unexpected connector response shape" in str(exc.value)
