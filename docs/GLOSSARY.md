# Plain-English glossary

Everything a judge might ask you to explain, in language you can say out loud
without a computer-science background. Read this once; you don't need to memorise
it.

---

## BM25 — "keyword search"

The classic way search engines rank results. It counts how many of your query's
words appear in each document, weights **rare** words more heavily than common
ones, and slightly penalises very long documents so they don't win just by being
big.

The thing to understand: **if the word isn't literally in the document, BM25
scores it near zero.** It matches characters, not meaning.

**Say it like this:** "BM25 is keyword matching — it finds documents that contain
the words you typed."

**Why it matters here:** a catfish rewords the bio. Different words, same
meaning. BM25 sees two unrelated documents.

---

## Embeddings — "meaning as numbers"

An embedding turns a piece of text into a long list of numbers. The useful
property: texts that *mean* similar things get *similar* numbers, even with no
words in common.

"Founder & digital asset trader" and "Entrepreneur, crypto investor" share almost
no words, but their number-lists land close together, because the model that
produced them was trained on meaning rather than spelling.

**Say it like this:** "An embedding converts text into coordinates, where things
that mean the same thing end up near each other."

In this project Elasticsearch generates these itself — that's what the
`semantic_text` field type does. There's no separate embedding service to run,
which is a genuine convenience worth mentioning.

---

## kNN — "find the nearest neighbours"

Short for *k nearest neighbours*. If embeddings put text at coordinates, kNN is
just "give me the k closest points to this one."

**Say it like this:** "kNN finds the closest matches by meaning. Embeddings put
the text on a map, kNN finds what's nearby."

**Combined:** "kNN over embeddings" = *find the bios that mean the same thing,
no matter what words they used.* That's the whole ring-detection mechanism.

---

## Hybrid search and RRF

You need both kinds of matching, because they catch different things:

| | Catches | Misses |
|---|---|---|
| Keyword (BM25) | The exact link they reused, an identical handle pattern, a specific emoji | The reworded bio |
| Semantic (kNN) | The reworded bio | Exact strings — vectors blur precise details |

**RRF** (Reciprocal Rank Fusion) merges two ranked lists into one. Each result
gets scored by its *position* in each list rather than by raw score, so you don't
have to make two incompatible scoring systems agree.

**Say it like this:** "I run both a keyword search and a meaning search, and
Elasticsearch fuses the two rankings into one list. One query, not two systems."

---

## `significant_text` — "which words are unusual here?"

This is the one that impresses people, so it's worth being able to explain.

Give Elasticsearch a **subset** of your documents (the flagged ones) and it
compares word frequencies in that subset against the **whole corpus**. Words that
are much more common inside the subset than outside get a high score.

The point is that **you never supply the word list.** You don't tell it "look for
crypto." It works out which words characterise the group.

**On your actual corpus it returned:**

```
coffee 0.458   crypto 0.458   gym 0.306   faith 0.306   founder 0.306
fitness 0.229  entrepreneur 0.229   espresso 0.229   investor 0.229
```

**Say it like this:** "I handed it the flagged accounts and asked which words are
statistically unusual compared to everything else in the index. It gave me the
fingerprint. I never wrote that list."

### Why "there's no version of this in Postgres"

Be precise, because a database person may push back. It is not that Postgres
*cannot* compute it — it's that this isn't a query there, it's a program you'd
write:

1. Pull every document in both groups out of the database
2. Tokenise the text yourself
3. Count term frequencies in the subset and in the background
4. Implement a significance score
5. Sort

In Elasticsearch it's one aggregation, computed where the inverted index already
lives, and the term statistics it needs are already maintained.

**Say it like this:** "Postgres has full-text search, but it has no notion of
'which terms are statistically over-represented in this subset.' You'd write that
yourself, and you'd be moving all the data out to do it."

---

## Percolator — "search, but backwards"

Normal search: documents are stored, you send a **query**, you get back matching
**documents**.

A percolator inverts it: you store the **queries**, you send a **document**, and
it tells you which stored queries match it.

**Say it like this:** "Instead of me asking 'which accounts match the scam
fingerprint,' I save the fingerprint as a standing query. Then every new profile
that comes in gets checked against it automatically, and it tells me. Monitoring
instead of searching."

**How you'd demo it** (roughly 15 lines of setup, only if you have spare time):

1. Create an index with a `percolator`-typed field
2. Store the fingerprint as a document in it — e.g. a query matching bios
   containing several of `crypto`, `founder`, `faith`, `espresso`
3. Send a fresh profile to the `_percolate` query
4. It returns the fingerprint's id → "this new profile tripped the alarm"

Realistically: **describe it as the roadmap, don't build it.** It's a strong
closing line and a weak use of your last twenty minutes.

---

## Why scraping works when the API doesn't

This is the sharpest question you'll get, and there's a clean answer.

Two different things get confused:

**Instagram's Graph API** — the official programmatic interface. It only lets you
read accounts you own or manage. There is no endpoint for "show me some other
person's profile." That door is closed by policy.

**The public web page** — when anyone, logged out, visits `instagram.com/nasa`,
the server returns that profile: bio, follower counts, recent posts, captions.
It's public by design.

Apify reads the second one. It runs headless browsers behind rotating proxies,
handles the anti-bot challenges, and returns structured JSON.

**Say it like this:** "The API only reads accounts you own, so it's not an option.
But the public profile page is public — anyone can load it in a browser. Apify
fetches that same page at scale and hands me structured data. What it's absorbing
is the anti-bot handling, the proxy rotation, and the pagination — the work that
has nothing to do with my actual idea."

Those two facts aren't in tension: **no usable API** and **the data is public**
are both true at once.

---

## "12 captions"

Apify's `instagram-scraper` returns, in a single response per profile: the bio,
follower and following counts, post count, verified flag, external link — and a
`latestPosts` array containing the **12 most recent posts**, each with its
caption text, hashtags, timestamp, and like count.

So "12 captions" means the text of their last 12 posts. That's the *evidence*
side of the comparison — the bio is the claim, the captions are what actually got
published.

**Say it like this:** "One call gives me the bio and the text of their last twelve
posts. The bio is what they claim; those twelve captions are what they can
actually show."

---

## "The verdict is indexed"

"Indexed" just means **saved into Elasticsearch as a searchable document.**

The model returns a structured result. That result gets written to the index. It's
now searchable and aggregatable, which is what makes the *next* profile
comparable to it.

The actual document:

```json
{
  "handle": "example",
  "bio": "Entrepreneur | Crypto | Coffee, gym, travel | God first",
  "bio_raw": "Entrepreneur | Crypto | Coffee, gym, travel | God first",
  "score": 3,
  "verdict": "Bio reads like an outdoors influencer; posts are indoors.",
  "ring_score": 0.89,
  "is_ring": true,
  "confidence": "high",
  "followers": 1203,
  "follows": 4400,
  "claims": [
    { "interest": "fitness", "claimed": 8, "evidence": 1,
      "receipt": "one gym photo, 2022" }
  ]
}
```

Two fields carry the same bio text on purpose: `bio` is the semantic field (used
for meaning search) and `bio_raw` is the keyword field (used for BM25 and
`significant_text`). Dropping either one silently removes a capability.

**Say it like this:** "The verdict isn't just printed — it's saved into
Elasticsearch. So the next profile I check gets compared against this one. The
index is the memory, and it grows every time I use the tool."
