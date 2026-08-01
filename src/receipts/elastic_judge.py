"""Judging through Elastic's managed LLM — no third-party API key required.

Elastic Serverless ships preconfigured LLM connectors, so the same Elasticsearch
API key that reads the index can also run the model. That removes the last
credential this project would otherwise need, and the spend with it.

Route matters. Two endpoints look like they should work and only one does:

    POST {es}/_inference/completion/.anthropic-claude-5-sonnet-completion
        -> 403 Forbidden for an API-key caller
    POST {es}/_inference/chat_completion/{id}/_stream
        -> 403 Forbidden for an API-key caller
    POST {kibana}/api/actions/connector/{id}/_execute
        -> works; this is the route Agent Builder uses

So requests go through Kibana's connector-execute API. Verified against a live
Serverless project: returns `model: anthropic-claude-5-sonnet` with token usage.

One capability is genuinely lost versus calling Anthropic directly: there is no
structured-output enforcement here. The Anthropic path constrains the response to
the `Verdict` schema at the API level; this path asks for JSON and validates what
comes back, with one repair attempt. That is a real downgrade, handled explicitly
rather than hidden.
"""

from __future__ import annotations

import json
import re

import httpx
from pydantic import ValidationError

from .config import INTERESTS, Config
from .models import Profile, Verdict

TIMEOUT_S = 120

SCHEMA_INSTRUCTION = """Return ONLY a single JSON object. No prose before or
after it, no markdown code fences, no explanation.

Shape:
{
  "score": integer 0-10,
  "verdict": "two blunt, specific sentences",
  "ring_score": number 0.0-1.0,
  "confidence": "low" | "medium" | "high",
  "claims": [
    {"interest": string, "claimed": integer 0-10,
     "evidence": integer 0-10, "receipt": "one short factual line"}
  ]
}"""

SYSTEM = """You are a blunt, funny catfish auditor.

Rules you never break:
- Cite only evidence present in the data you are given.
- Never invent a receipt, a quote, or a count. If the evidence is thin, say so
  and set confidence to "low".
- A low score means "unverified or inconsistent". It never means "proven fake".
- Never speculate about anyone's gender, sexuality, race, or relationship status.
- You judge only what this person published about themselves. You do not profile
  the people they follow."""


class ElasticJudgeError(RuntimeError):
    """Raised when the managed LLM cannot be reached or its output is unusable."""


def _prompt(profile: Profile, similar: list[dict]) -> str:
    captions = (
        "\n".join(f"- {c[:400]}" for c in profile.captions[:30])
        or "(none — this account has no captioned posts)"
    )
    return f"""{SCHEMA_INSTRUCTION}

PROFILE
handle: @{profile.handle}
bio: {profile.bio or "(empty)"}
followers: {profile.followers} | follows: {profile.follows} | posts: {profile.post_count}
verified: {profile.verified} | account created recently: {profile.joined_recently}
external link: {profile.external_url or "(none)"}

CAPTIONS FROM RECENT POSTS
{captions}

SIMILAR ACCOUNTS ALREADY IN THE INDEX
Found by semantic search over every profile scored so far. High similarity with
DIFFERENT wording is the signal that matters — a mass-produced bio reworded, not
a coincidental phrase match.
{json.dumps(similar, indent=2) if similar else "(none — first profile in the index)"}

TASK
For each interest in [{", ".join(INTERESTS)}]:
  claimed  = 0-10, how strongly the BIO asserts it
  evidence = 0-10, how strongly the CAPTIONS actually prove it
  receipt  = one short factual line, quoting or counting real posts

Then:
  ring_score = 0-1. High only when the wording differs but the meaning, claimed
               interests, and structure line up with the similar accounts above.
               An empty similar list means 0.
  score      = 0-10 overall authenticity. Penalise a high ring_score heavily.
               Also penalise very few posts, a brand-new account, or a
               follower/follows ratio typical of a farmed account.
  confidence = how much evidence backed this call."""


def _call(cfg: Config, messages: list[dict]) -> str:
    """Execute the Kibana connector and return the assistant's text."""
    url = (
        f"{cfg.kibana_endpoint}/api/actions/connector/"
        f"{cfg.elastic_llm_connector}/_execute"
    )
    try:
        response = httpx.post(
            url,
            headers={
                "Authorization": f"ApiKey {cfg.es_api_key}",
                "kbn-xsrf": "true",
                "Content-Type": "application/json",
            },
            json={
                "params": {
                    "subAction": "unified_completion",
                    "subActionParams": {"body": {"messages": messages}},
                }
            },
            timeout=TIMEOUT_S,
        )
    except httpx.HTTPError as exc:
        raise ElasticJudgeError(f"could not reach Kibana at {url}: {exc}") from exc

    if response.status_code != 200:
        raise ElasticJudgeError(
            f"connector returned {response.status_code}: {response.text[:300]}"
        )

    payload = response.json()
    if payload.get("status") != "ok":
        raise ElasticJudgeError(f"connector reported failure: {str(payload)[:300]}")

    try:
        return payload["data"]["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ElasticJudgeError(
            f"unexpected connector response shape: {str(payload)[:300]}"
        ) from exc


def _extract_json(text: str) -> dict:
    """Pull a JSON object out of the reply.

    Needed because this route has no structured-output enforcement: the model is
    asked for bare JSON but may still wrap it in fences or add a sentence.
    """
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.+?)\s*```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Fall back to the outermost brace pair.
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ElasticJudgeError(f"no JSON object in reply: {text[:200]!r}")
    return json.loads(text[start : end + 1])


def judge_via_elastic(
    profile: Profile, similar: list[dict], cfg: Config
) -> Verdict:
    """Score a profile using Elastic's managed LLM.

    Makes one repair attempt if the first reply fails validation, feeding the
    error back so the model can correct it. Two attempts total, then it gives up
    rather than looping.
    """
    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": _prompt(profile, similar)},
    ]

    reply = _call(cfg, messages)
    try:
        return Verdict.model_validate(_extract_json(reply))
    except (ValidationError, ElasticJudgeError, json.JSONDecodeError) as first_error:
        repair = messages + [
            {"role": "assistant", "content": reply},
            {
                "role": "user",
                "content": (
                    f"That failed validation: {first_error}\n\n"
                    "Return ONLY the corrected JSON object. No prose, no fences."
                ),
            },
        ]
        retry = _call(cfg, repair)
        try:
            return Verdict.model_validate(_extract_json(retry))
        except (ValidationError, ElasticJudgeError, json.JSONDecodeError) as exc:
            raise ElasticJudgeError(
                f"model output failed validation twice. First: {first_error}. "
                f"Second: {exc}"
            ) from exc
