# Setup guide

Everything needed to run Receipts from a clean clone. Roughly 10 minutes.

## Prerequisites

| Thing | Where | Cost |
|---|---|---|
| Apify account + API token | console.apify.com → Settings → Integrations | Free tier: $5/mo of platform credits, no card |
| Elasticsearch Serverless project | cloud.elastic.co | Free trial |
| Anthropic API key | platform.claude.com → Settings → API keys | Pay-as-you-go, ~$0.04 per profile |

**The Anthropic key is optional.** Two ways to skip it:

- `--dry-run` on any `score` command uses an offline stub verdict. The scrape,
  the recall, and the indexing all still run — only the judgment is stubbed.
- If you're driving this from Kibana instead of Python, Agent Builder on
  Serverless ships with a preconfigured **Elastic Managed LLM** connector that
  needs no third-party key at all.

## 1. Credentials

```bash
cp .env.example .env
```

Fill in four values:

**`APIFY_TOKEN`** — console.apify.com → Settings → Integrations → Personal API
tokens.

**`ES_ENDPOINT`** — cloud.elastic.co → your project → **Endpoints**. You want the
*Elasticsearch* URL, **not** the Kibana URL you browse. It looks like:

```
https://<project>.es.<region>.<cloud>.elastic.cloud:443
```

This is the single most common setup mistake — the Kibana URL will fail to
connect.

**`ES_API_KEY`** — in Kibana, search "API keys" in the global search bar →
Create API key → copy the **encoded** value (not the ID). It is shown once.

**`ANTHROPIC_API_KEY`** — optional, see above.

`.env` is gitignored. Don't commit it.

## 2. Install

```bash
make setup      # creates .venv, installs dependencies
make test       # 48 tests, no credentials or network needed
```

If `make test` passes you have a working install. The tests deliberately require
no cluster and no keys, so this isolates environment problems from credential
problems.

## 3. Create the index

```bash
make init
```

This applies `infra/mappings.json`. Two field choices matter:

- **`bio` is `semantic_text`** — Elasticsearch generates and stores the
  embeddings itself at index time. On Serverless it auto-resolves an inference
  endpoint (ours picked `.jina-embeddings-v5-text-small`) so there is nothing to
  configure and no embedding service to run.
- **`bio_raw` is plain `text`** — the same bio text, stored a second time.
  Required because BM25 and the `significant_text` aggregation need an *analyzed*
  field; they cannot operate on `semantic_text`. Dropping either field silently
  removes a capability.

Verify:

```bash
curl -s "$ES_ENDPOINT/receipts/_mapping" -H "Authorization: ApiKey $ES_API_KEY"
```

## 4. Score a profile

```bash
make score HANDLE=natgeo              # with an Anthropic key
# or
PYTHONPATH=src .venv/bin/python -m receipts.cli score natgeo --dry-run
```

Expect **10–40 seconds**, mostly Apify. Public accounts only — a private handle
raises `PrivateProfileError` rather than being partially scraped.

## 5. Build a corpus

Ring detection compares a profile against everything already indexed, so a single
document has nothing to work with. Score 10+ accounts first:

```bash
for h in natgeo patagonia rei gopro bonappetitmag nike spotify sephora airbnb redbull; do
  PYTHONPATH=src .venv/bin/python -m receipts.cli score "$h" --dry-run
done
```

Writes are **idempotent** — the handle is the document id, so re-running updates
rather than duplicating. (Without this an account appears twice, then shows up in
its own recall results and matches itself as a near-duplicate.)

## 6. Seed the labelled demo ring

`significant_text` and the date histogram both need a *flagged cohort* to contrast
against. Real scraped accounts are not a catfish ring, and flagging them as one
would put a fabricated claim into the index.

```bash
PYTHONPATH=src .venv/bin/python -m receipts.cli seed-ring
```

This indexes 12 bios built from one template. **Every one is marked
`synthetic: true`** so it is filterable and cannot be mistaken for scraped data:

```
GET receipts/_search { "query": { "term": { "synthetic": true } } }
```

Remove them any time with `drop-synthetic`.

## 7. Run the queries

```bash
PYTHONPATH=src .venv/bin/python -m receipts.cli fingerprint   # significant_text
PYTHONPATH=src .venv/bin/python -m receipts.cli histogram     # creation clustering
PYTHONPATH=src .venv/bin/python -m receipts.cli gaps          # nested aggregation
PYTHONPATH=src .venv/bin/python -m receipts.cli similar "some bio text"
```

## Commands

| Command | What it does |
|---|---|
| `init` | Create the index from `infra/mappings.json` |
| `score <handle>` | scrape → recall → judge → remember. `--dry-run` skips the model |
| `similar <text>` | Hybrid search (semantic + exact link) against the corpus |
| `fingerprint` | `significant_text`: terms over-represented in flagged bios |
| `histogram` | Creation-date clustering of the flagged cohort |
| `gaps` | Average claimed-vs-evidence gap per interest |
| `seed-ring` | Index the labelled synthetic ring cohort |
| `drop-synthetic` | Delete every `synthetic: true` document |

## Kibana

For the visual: **claimed vs. evidence per interest** is a bar chart over the
`claims` nested field, using the `gaps` aggregation. One chart is enough — the
terminal output carries the detail.

## Troubleshooting

| Symptom | Cause |
|---|---|
| Connection refused / 401 | `ES_ENDPOINT` is the Kibana URL, or you copied the API key *ID* instead of the encoded key |
| `400 ... input[0] must be present and not empty` | An account with an empty bio reached `semantic_text`. Fixed in `to_document`; if you see it, an empty string is being written to `bio` |
| `408` from Apify | The sync endpoint caps at 300s. Lower `resultsLimit` |
| Profile matches itself in recall | Duplicate documents for one handle. `remember()` keys on handle to prevent it |
| `fingerprint` returns nothing | No `is_ring: true` documents. Run `seed-ring`, or score with a real model |
| `histogram` returns nothing | Apify does not expose account creation dates — only a `joinedRecently` boolean. Only seeded records carry `first_post_at` |

## Known limitations

- **No account creation date.** Apify's Instagram scraper returns a
  `joinedRecently` boolean, not a date. The `joinedRecently` flag is used as a
  risk signal; the date histogram only works on seeded records.
- **Scrape latency varies widely** — measured 10.1s to 37.3s across 10 runs on
  the same actor. Don't build a UI that promises a fixed number.
- **The model can still be wrong.** The prompt forbids inventing evidence and
  requires a confidence level, and thin evidence returns `confidence: low`. That
  reduces the problem; it doesn't eliminate it.
