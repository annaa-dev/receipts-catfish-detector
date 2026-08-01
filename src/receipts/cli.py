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
import time

from .config import Config, ConfigError
from .memory import Memory, load_mapping
from .models import Profile, Verdict
from .pipeline import score_handle
from .signals import NOTABLE_GAP, likelihood, risk_signals


def _es_client(cfg: Config):
    from elasticsearch import Elasticsearch

    return Elasticsearch(cfg.es_endpoint, api_key=cfg.es_api_key)


def _bar(value: int, width: int = 10) -> str:
    filled = max(0, min(width, value))
    return "█" * filled + "·" * (width - filled)


def render(
    handle: str,
    verdict: Verdict,
    similar: list[dict],
    profile: Profile | None = None,
) -> str:
    """Render a verdict that explains itself.

    The old version printed a score with no account of where it came from. Three
    things are now explicit: what the bio *claims*, what the posts actually
    *show*, and which checks drove the number.
    """
    band, because = ("", "")
    signals: list = []
    if profile is not None:
        signals = risk_signals(profile, verdict)
        band, because = likelihood(verdict, signals)

    lines = ["", f"  @{handle}"]
    if band:
        lines.append(f"  {band} — {because}")
    lines += [
        f"  authenticity {verdict.score}/10"
        f"   ·   template match {verdict.ring_score:.2f}"
        f"   ·   confidence {verdict.confidence}",
        "",
        f"  {verdict.verdict}",
        "",
        "  INTERESTS — what the bio says vs what the posts prove",
        f"  {'interest':<11} {'SAYS':<12} {'POSTS SHOW':<12} {'GAP':>4}  evidence",
    ]
    for claim in sorted(verdict.claims, key=lambda c: c.gap, reverse=True):
        marker = "  <" if claim.gap >= NOTABLE_GAP else "   "
        lines.append(
            f"  {claim.interest:<11} {_bar(claim.claimed)}  {_bar(claim.evidence)}  "
            f"{claim.gap:>+4}  {claim.receipt}{marker}"
        )
    if any(c.gap >= NOTABLE_GAP for c in verdict.claims):
        lines.append("  (< marks a claim the posts do not support)")

    if signals:
        lines += ["", "  WHY THIS SCORE"]
        for signal in signals:
            mark = "✗" if signal.fired else "·"
            lines.append(f"    {mark} {signal.name:<20} {signal.detail}")

    if similar:
        lines += ["", f"  SIMILAR BIOS ALREADY IN THE INDEX ({len(similar)})"]
        for row in similar[:5]:
            flag = "  [flagged]" if row.get("previously_flagged") else ""
            lines.append(
                f"    {row['similarity']:.3f}  @{row['handle']}{flag}"
            )
        lines.append("    (similarity is by meaning, not shared words)")

    lines += [
        "",
        "  A low score means the bio's claims are UNVERIFIED — not that the person",
        "  is lying. This measures whether posts back up a bio, nothing more.",
        "",
    ]
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
    sub.add_parser(
        "seed-ring",
        help="index the labelled synthetic ring cohort (marked synthetic=true)",
    )
    sub.add_parser("drop-synthetic", help="remove every synthetic=true document")
    sub.add_parser("histogram", help="creation-date clustering of flagged accounts")

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
            start = time.monotonic()

            def stage(message: str) -> None:
                print(f"  [{time.monotonic() - start:5.1f}s] {message}", flush=True)

            profile, verdict, similar = score_handle(
                args.handle, cfg, memory, dry_run=args.dry_run, on_stage=stage
            )
            print(render(profile.handle, verdict, similar, profile))
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
        elif args.command == "seed-ring":
            from .seed import ring_documents

            docs = ring_documents()
            for doc in docs:
                memory.remember(doc)
            print(
                f"indexed {len(docs)} synthetic ring documents "
                "(every one marked synthetic=true). "
                "Remove them with: receipts drop-synthetic"
            )
        elif args.command == "drop-synthetic":
            client = _es_client(cfg)
            result = client.delete_by_query(
                index=cfg.index,
                body={"query": {"term": {"synthetic": True}}},
                refresh=True,
            )
            print(f"deleted {result['deleted']} synthetic documents")
        elif args.command == "histogram":
            result = memory.creation_histogram()
            buckets = (
                result.get("aggregations", {}).get("created", {}).get("buckets", [])
            )
            if not buckets:
                print(
                    "No creation dates available. Note: Apify's instagram-scraper "
                    "does not expose account creation date — only a joinedRecently "
                    "boolean. Only seeded synthetic records carry first_post_at."
                )
            for bucket in buckets:
                if bucket["doc_count"]:
                    bar = "█" * bucket["doc_count"]
                    print(f"{bucket['key_as_string'][:10]}  {bar} {bucket['doc_count']}")
        return 0

    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # surface the real cause, don't swallow it
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
