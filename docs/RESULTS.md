# Measured results

Every number here came from a real run against a live Elasticsearch Serverless
cluster and real Apify scrapes. Nothing is estimated, and every measurement below
has the command that produces it, so it can be checked rather than taken on
trust.

**Environment**

| | |
|---|---|
| Elasticsearch | Serverless **9.6.0** |
| Embeddings | `.jina-embeddings-v5-text-small`, auto-resolved by `semantic_text` — no inference endpoint configured, no embedding service deployed |
| Scraper | `apify/instagram-scraper` (actor id `shu8hvrXbJbY3Eb9W`) |
| Apify runs to date | 31, costing **$0.049** total |
| Tests | **57**, requiring no network and no credentials |

---

## 1. The core claim, measured

The project's argument is that finding "same meaning, different words" requires
vector search and cannot be done with keyword matching. That is testable, so here
is the test.

Three bios were indexed. **A** is the query. **B** is A reworded — same claimed
interests, same structure, almost entirely different words. **C** is unrelated.

```
A (query)  Entrepreneur. Crypto investor. Coffee, gym, travel. God first.
B (reword) Founder & digital asset trader. Espresso, fitness, seeing the world. Faith first.
C (other)  Marine biologist studying coral reef restoration in the Florida Keys.
```

The only word A and B share is **"first"**.

### Semantic search (`semantic_text`, kNN over embeddings)

```
0.8384  B   <- the rewording
0.5776  C   <- unrelated bio
```

B is found and cleanly separated from an unrelated bio. This is the mechanism the
ring detection depends on.

### BM25 (keyword) — same query, same documents

```
14.1033  A   <- the identical document
 0.9447  B   <- the rewording
```

Two things to notice. BM25 *does* return B, but only because both bios contain the
word "first" — that is the entire signal. And it ranks the identical document
**about 15× higher**, so at corpus scale the rewording is buried beneath exact
matches.

**Conclusion:** keyword search cannot do this job, because the copies are
deliberately reworded. That is why Elasticsearch is load-bearing here rather than
decorative.

Reproduce it:

```bash
PYTHONPATH=src .venv/bin/python -m receipts.cli similar \
  "Entrepreneur. Crypto investor. Coffee, gym, travel. God first."
```

---

## 2. The fingerprint the aggregation derived by itself

`significant_text` compares word frequencies in the flagged cohort against the
whole corpus and returns the terms that are statistically over-represented. **No
word list was written by hand, and no language model runs in this step.**

```
$ receipts fingerprint

coffee                   score 0.8750
crypto                   score 0.8750
gym                      score 0.5833
faith                    score 0.5833
founder                  score 0.5833
first                    score 0.4375
fitness                  score 0.4375
digital                  score 0.4375
entrepreneur             score 0.4375
espresso                 score 0.4375
investor                 score 0.4375
always                   score 0.4375
```

### The signal strengthens as the corpus grows

The same aggregation was run at two corpus sizes. More real accounts means more
background to contrast against, so the flagged terms separate more sharply:

| term | 11 real accounts | 21 real accounts |
|---|---|---|
| coffee | 0.458 | **0.875** |
| crypto | 0.458 | **0.875** |
| gym | 0.306 | **0.583** |
| faith | 0.306 | **0.583** |
| founder | 0.306 | **0.583** |

This is the argument for memory-as-architecture, measured: the tool gets better
at identifying fakes as it accumulates real examples.

---

## 3. Corpus

```
68 documents = 21 curated real  +  35 search-sourced real  +  12 labelled synthetic
0 duplicates
```

The 35 search-sourced accounts came from Apify's user-search mode across three
terms ("crypto investor", "forex mentor", "day trader") and are marked
`search_sourced: true`. They were not hand-picked — the point was to test the
detector on accounts neither the author nor the tool chose.

**Real accounts, all with 12 scraped captions each:**

```
adidas, airbnb, bonappetitmag, gopro, lululemon, nasa, natgeo, netflix,
nike, patagonia, redbull, rei, sephora, spotify, starbucks, strava,
tastemade, themuseumofmodernart, thenorthface, vans, yeti
```

**On the synthetic records.** `significant_text` and the date histogram both need
a *flagged cohort* to contrast against, and real scraped brand accounts are not a
catfish ring. Flagging them as one would put a fabricated claim into the index
where a later query would read it back as fact. So the flagged cohort is
generated — twelve bios built from one template — and **every one carries
`synthetic: true`**, making it filterable and impossible to confuse with scraped
data:

```
GET receipts/_search { "query": { "term": { "synthetic": true } } }
```

---

## 4. Scrape latency

Measured across three separate batches, same actor, same settings:

| batch | min | median | max |
|---|---|---|---|
| 10 accounts | 10.1s | 19.6s | 37.3s |
| 10 accounts | 4.2s | 17.9s | 30.9s |
| single runs | 5.4s | — | 37.1s |

**Range 4.2s to 37.3s — a 9× spread on identical requests.** Worth stating
plainly because the first run measured 5.4s, and treating that as typical would
have been wrong. Anything built on this should not promise a fixed number.

---

## 5. Creation-date clustering, and its limitation

```
$ receipts histogram

2026-05-04  ████ 4
2026-05-11  ██ 2
2026-05-18  ██ 2
2026-05-25  ██ 2
2026-06-01  ██ 2
```

⚠️ **This runs on the synthetic records only.** Apify's Instagram scraper does
**not** expose account creation dates — it returns a `joinedRecently` boolean and
nothing more. The boolean *is* used as a risk signal in scoring; the histogram
demonstrates the query shape, not a finding about real accounts. Doing this on
real data would need a different signal, such as earliest post date, which means a
deeper crawl.

Documented rather than quietly omitted, because a reviewer would reasonably ask
where the dates came from.

---

## 6. Four bugs that only real data exposed

None of these were visible against fixtures. Each has a regression test.

**Accounts with no bio returned HTTP 400.** `semantic_text` forwards the field
value to an inference endpoint at index time, and that endpoint rejects empty
input. `@airbnb` has no bio, so it crashed the run. The field is now omitted
rather than written blank, and semantic recall is skipped when there is nothing to
match.

**Re-scoring created duplicates, and then profiles matched themselves.** Writes
supplied no document id, so checking an account twice left two documents. Both
came back in recall, and the account matched itself as a near-duplicate — the
exact false positive ring detection exists to prevent. `@patagonia` had two
copies. The handle is now the document id, making writes idempotent.

**Recall reported an uninterpretable similarity.** RRF returns a *fused rank*
score, which put a rank-1 match around 0.09 — a number that reads as "unrelated"
to anyone looking at the output. Recall now uses the semantic query for a genuine
0–1 similarity, with exact external-link reuse checked separately so nothing is
lost by not fusing.

**Nonexistent handles were scored as real accounts.** A handle that does not exist
does not return an empty list or an error status. It returns HTTP 200 with a
populated row:

```json
{"error": "not_found", "errorDescription": "Post does not exist",
 "username": "...", "url": "..."}
```

That row parsed into a profile with an empty bio and zero followers, which was
then scored and indexed as real — and because writes key on the handle, it
overwrote whatever was stored there. Found when a demo script scraped a synthetic
handle and destroyed the seeded record. Now raises `ProfileNotFoundError`. This is
the failure a live audience produces immediately, with the first mistyped handle.

---

## 7. Reproducing all of it

```bash
make setup                        # venv + dependencies
make test                         # 57 tests, no credentials needed
cp .env.example .env              # then fill in (see GUIDE.md)
make init                         # create the index
make score HANDLE=natgeo          # one profile, end to end

PYTHONPATH=src .venv/bin/python -m receipts.cli seed-ring
PYTHONPATH=src .venv/bin/python -m receipts.cli fingerprint
PYTHONPATH=src .venv/bin/python -m receipts.cli histogram

./scripts/demo.sh                 # paced walkthrough
```

`make test` needs no cluster and no keys, so it isolates environment problems from
credential problems.

---

## 8. What is not proven

Stated plainly, because the rest of this document is measured and this part
is not.

**The LLM judge has never run against the real API.** Every verdict currently in
the index came from the offline stub in `judge.py`, which counts whether an
interest word appears in a caption. The scrape, the recall, the indexing, the
aggregations, and the idempotency are all verified against live data. The one path
not exercised end to end is the model call. `messages.parse(output_format=...)` was
confirmed present in the installed SDK (0.120.2), but the request was never sent.

**No Elastic Workflow or Agent Builder tool is built.** The workflow YAML and agent
tool configuration exist as specifications. The `ai.prompt` step's `with:` schema
is taken from Elastic's documentation and has not been executed.

**No real account has been confirmed as a bot.** Section 9 shows real accounts
whose bios are near-duplicates, which is a measurable fact. It is not the same
claim as "these are bots", and the tool does not make that claim.

---

## 9. Does it work on real accounts? Calibration

The threshold mattered more than expected, and getting it wrong in the safe
direction hid a true positive.

35 real accounts were scraped via Apify's user-search mode and checked against
each other. At a 0.85 similarity threshold — chosen from how tightly the synthetic
template cohort clusters — **nothing was flagged**. Dropping the threshold surfaced
genuine near-duplicates:

```
0.831
  A: 💎Web3 инвестор / Отбираю сильные крипто-проекты / ...
  B: 💶Best investor in 🇺🇸#crypto #cryptonews #arbitrage / 🔥#web3🤑 / ...

0.826
  A: 📊 Forex Trading & Mentorship / Analysis • Strategies • Education / Join the community ⬇️
  B: 📈 Simple Forex Tips 🤝 Official Deriv Partner 👥 1500+ Strong Trading Community 👇

0.821
  A: 📊 Forex Trading & Mentorship / Analysis • Strategies • Education / Join the community ⬇️
  B: 📈 FX TRADER / 📚 DM for Private 1:1 Mentorship
```

Measured operating range:

| what | semantic similarity |
|---|---|
| Unrelated bios | ~0.58 |
| **Real templated accounts** | **0.80 – 0.83** |
| Synthetic template cohort | 0.84 – 0.95 |

Two conclusions. The separation is real and usable — templated bios sit well above
unrelated ones. And a threshold tuned on synthetic data was **too strict for real
accounts**, which is the kind of error that produces a confident "nothing found"
while true positives sit just underneath it.

**What this does and does not establish.** That these bios are near-duplicates is
measured. Whether the accounts behind them are bots, spam, or simply people in the
same niche copying a popular format is **not** established, and the tool does not
claim it. The output says the claims are *unverified*, never that a person is
fake — a distinction that matters most precisely when pointing at real accounts.

---

## Screenshots

Terminal and API output above is verbatim and reproducible, which is stronger
evidence than an image. If you want Kibana screenshots as well, these four carry
the most:

1. **Discover** showing the 33 documents with `handle`, `bio_raw`, `score`, `is_ring`
2. **Dev Tools** running the semantic query and the keyword query back to back on
   the same input — the clearest single view of the whole argument
3. **Dev Tools** running the `significant_text` aggregation
4. **The index mapping**, showing `bio` as `semantic_text` beside `bio_raw` as `text`

Drop them in `docs/images/` and reference them here.
