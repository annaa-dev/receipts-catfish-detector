"""Command line entry point.

    receipts init                 create the index from infra/mappings.json
    receipts score @handle        scrape -> recall -> judge -> remember
    receipts similar @handle      hybrid search against the corpus
    receipts fingerprint          what do flagged bios have in common?
    receipts gaps                 avg claimed-vs-evidence per interest
"""

from __future__ import annotations

import argparse
import json
import sys

from .config import Config, ConfigError
from .memory import Memory, load_mapping
from .models import Verdict
from .pipeline import score_handle


def _es_client(cfg: Config):
    from elasticsearch import Elasticsearch

    return Elasticsearch(cfg.es_endpoint, api_key=cfg.es_api_key)


def _bar(value: int, width: int = 10) -> str:
    filled = max(0, min(width, value))
    return "█" * filled + "·" * (width - filled)


def render(handle: str, verdict: Verdict, similar: list[dict]) -> str:
    lines = [
        "",
        f"@{handle} — {verdict.score}/10  (confidence: {verdict.confidence})",
        verdict.verdict,
        "",
        f"{'interest':<12} {'claims':<12} {'evidence':<12} receipt",
    ]
    for claim in sorted(verdict.claims, key=lambda c: c.gap, reverse=True):
        lines.append(
            f"{claim.interest:<12} {_bar(claim.claimed)}  {_bar(claim.evidence)}  "
            f"{claim.receipt}"
        )

    if similar:
        lines += ["", f"Similar accounts in index: {len(similar)}"]
        for row in similar[:5]:
            flag = " [previously flagged]" if row.get("previously_flagged") else ""
            lines.append(
                f"  @{row['handle']}  similarity {row['similarity']}{flag}"
            )
    if verdict.ring_score >= 0.7:
        lines += [
            "",
            f"⚠ ring_score {verdict.ring_score:.2f} — this bio reads as a reworded "
            "copy of accounts already indexed.",
        ]
    lines.append("")
    lines.append(
        "A low score means unverified or inconsistent. It does not mean proven fake."
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="receipts", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="create the index")

    p_score = sub.add_parser("score", help="score a public handle")
    p_score.add_argument("handle")
    p_score.add_argument(
        "--dry-run",
        action="store_true",
        help="skip the model call and use the offline stub verdict",
    )

    p_similar = sub.add_parser("similar", help="hybrid search against the corpus")
    p_similar.add_argument("text", help="bio text to match")

    sub.add_parser("fingerprint", help="terms over-represented in flagged bios")
    sub.add_parser("gaps", help="average claimed-vs-evidence gap per interest")

    args = parser.parse_args(argv)

    try:
        if args.command == "init":
            cfg = Config.load(require=("es",))
            client = _es_client(cfg)
            client.indices.create(index=cfg.index, **load_mapping())
            print(f"created index: {cfg.index}")
            return 0

        if args.command == "score":
            require = ("apify", "es") if args.dry_run else ("apify", "es", "claude")
            cfg = Config.load(require=require)
            memory = Memory(_es_client(cfg), cfg.index)
            profile, verdict, similar = score_handle(
                args.handle, cfg, memory, dry_run=args.dry_run
            )
            print(render(profile.handle, verdict, similar))
            return 0

        cfg = Config.load(require=("es",))
        memory = Memory(_es_client(cfg), cfg.index)

        if args.command == "similar":
            print(json.dumps(memory.hybrid_search(args.text), indent=2, default=str))
        elif args.command == "fingerprint":
            result = memory.ring_fingerprint()
            buckets = result.get("aggregations", {}).get("fingerprint", {}).get(
                "buckets", []
            )
            if not buckets:
                print(
                    "No flagged accounts yet. Score more profiles, or seed a corpus "
                    "— the fingerprint needs a flagged set to contrast against."
                )
            for bucket in buckets:
                print(f"{bucket['key']:<24} score {bucket['score']:.4f}")
        elif args.command == "gaps":
            print(json.dumps(memory.gap_by_interest(), indent=2, default=str))
        return 0

    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # surface the real cause, don't swallow it
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
