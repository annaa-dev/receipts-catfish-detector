#!/usr/bin/env bash
# Paced demo. Press Enter between beats so you control the timing on stage.
#
#   ./scripts/demo.sh              run the scripted beats
#   ./scripts/demo.sh somehandle   ...then live-scrape a handle at the end
#
# Every number this prints is computed live against your cluster.

set -uo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH=src
PY=.venv/bin/python

beat() { printf '\n\033[1;36m── %s\033[0m\n\n' "$1"; }
pause() { printf '\n\033[2m[Enter]\033[0m'; read -r _; }

clear
printf '\033[1m  RECEIPTS — catfish detector\033[0m\n  Apify · Elasticsearch · LLM\n'
pause

# ─────────────────────────────────────────────────────────────────────────────
beat "1. The memory. This is what makes a verdict possible."
$PY - <<'PY'
from receipts.config import Config
from elasticsearch import Elasticsearch
cfg = Config.load(require=("es",))
es = Elasticsearch(cfg.es_endpoint, api_key=cfg.es_api_key, request_timeout=60)
es.indices.refresh(index=cfg.index)
total = es.count(index=cfg.index)["count"]
syn = es.count(index=cfg.index, query={"term": {"synthetic": True}})["count"]
print(f"  {total} profiles indexed  =  {total - syn} really scraped from Instagram"
      f"  +  {syn} labelled synthetic")
res = es.search(index=cfg.index, body={
    "size": 6, "_source": ["handle", "bio_raw"],
    "query": {"bool": {"must_not": [{"term": {"synthetic": True}}]}},
    "sort": [{"followers": "desc"}]})
print("\n  real accounts, largest first:")
for h in res["hits"]["hits"]:
    s = h["_source"]
    print(f"    @{s['handle']:<15} {(s.get('bio_raw') or '(no bio)')[:50]}")
PY
pause

# ─────────────────────────────────────────────────────────────────────────────
beat "2. THE POINT. Two bios that mean the same thing, sharing one word."
cat <<'TXT'
  A:  "Entrepreneur | Crypto investor | Coffee, gym, travel | God first"
  B:  "Founder & digital asset trader. Espresso, fitness, seeing the world.
       Faith first."

  The only word they share is "first".
TXT
pause

beat "   Keyword search (BM25) — looks for matching words"
$PY - <<'PY'
from receipts.config import Config
from receipts.memory import Memory
from elasticsearch import Elasticsearch
cfg = Config.load(require=("es",))
mem = Memory(Elasticsearch(cfg.es_endpoint, api_key=cfg.es_api_key,
                           request_timeout=60), cfg.index)
A = "Entrepreneur | Crypto investor | Coffee, gym, travel | God first"
for h in mem.keyword_search(A, size=3)["hits"]["hits"]:
    print(f"    {h['_score']:6.3f}  @{h['_source']['handle']}")
print("\n    ^ ranks by shared words. The rewrite barely registers.")
PY
pause

beat "   Semantic search — looks for matching MEANING"
$PY - <<'PY'
from receipts.config import Config
from receipts.memory import Memory
from elasticsearch import Elasticsearch
cfg = Config.load(require=("es",))
mem = Memory(Elasticsearch(cfg.es_endpoint, api_key=cfg.es_api_key,
                           request_timeout=60), cfg.index)
A = "Entrepreneur | Crypto investor | Coffee, gym, travel | God first"
for h in mem.semantic_search(A, size=5)["hits"]["hits"]:
    flag = "  <-- template cohort" if h["_source"].get("is_ring") else ""
    print(f"    {h['_score']:6.3f}  @{h['_source']['handle']}{flag}")
print("\n    ^ finds the reworded copies. Different words, same meaning.")
print("      THIS is why the project needs Elasticsearch and not a database.")
PY
pause

# ─────────────────────────────────────────────────────────────────────────────
beat "3. A verdict on an account whose bio is a template"
# Renders straight from the index. Deliberately does NOT call `score`: synthetic
# handles don't exist on Instagram, and scraping one returns an error row that
# would overwrite the seeded document.
$PY - <<'PY'
from receipts.cli import render
from receipts.config import Config
from receipts.models import Profile, Verdict
from elasticsearch import Elasticsearch
cfg = Config.load(require=("es",))
es = Elasticsearch(cfg.es_endpoint, api_key=cfg.es_api_key, request_timeout=60)
doc = es.get(index=cfg.index, id="synthetic_ring_04")["_source"]
profile = Profile(handle=doc["handle"], bio=doc["bio_raw"], captions=[],
                  followers=doc["followers"], follows=doc["follows"],
                  post_count=doc["post_count"], joined_recently=True,
                  external_url=doc.get("external_url", ""))
verdict = Verdict(score=doc["score"], verdict=doc["verdict"],
                  ring_score=doc["ring_score"], confidence=doc["confidence"],
                  claims=doc["claims"])
mem_hits = es.search(index=cfg.index, body={"size": 4,
    "_source": ["handle", "bio_raw", "is_ring"],
    "query": {"bool": {"must": [{"semantic": {"field": "bio", "query": doc["bio_raw"]}}],
                       "must_not": [{"term": {"handle": doc["handle"]}}]}}})
similar = [{"handle": h["_source"]["handle"], "bio": h["_source"].get("bio_raw", ""),
            "similarity": round(h["_score"], 3),
            "previously_flagged": bool(h["_source"].get("is_ring"))}
           for h in mem_hits["hits"]["hits"]]
print(render(profile.handle, verdict, similar, profile))
PY
pause

# ─────────────────────────────────────────────────────────────────────────────
beat "4. What do the flagged accounts have in common? Ask Elasticsearch."
echo "  significant_text: which words are statistically unusual in the flagged set,"
echo "  compared with every other account in the index?"
echo
$PY -m receipts.cli fingerprint | sed 's/^/    /'
echo
echo "    I never wrote that word list. The aggregation derived it."
echo "    No language model in this step at all."
pause

# ─────────────────────────────────────────────────────────────────────────────
if [ "$#" -ge 1 ]; then
  beat "5. LIVE — scraping @$1 from Instagram right now"
  echo "  This takes about 20 seconds. It's fetching the profile and the last"
  echo "  twelve captions, then searching everything already indexed for bios"
  echo "  that mean the same thing."
  echo
  time $PY -m receipts.cli score "$1" --dry-run
fi

printf '\n\033[1;36m── Done.\033[0m\n'
echo "  A low score means the claims are UNVERIFIED — not that anyone is lying."
echo
