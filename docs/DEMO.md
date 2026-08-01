# Receipts — 5-minute demo script

On-screen text stays minimal; the detail lives in what you say. Plain-English
explanations of any jargon are in [GLOSSARY.md](GLOSSARY.md).

**Every number in here is measured from real runs.** Nothing is estimated.

---

## Slide 1 — Title (0:00–0:20)

> # Receipts
> ### A catfish detector
> Apify · Elasticsearch · LLM

**Say:** "Show of hands — who's talked to someone online and wondered whether
they're actually who they say they are? Right. I built the thing that checks."

---

## Slide 2 — The problem (0:20–0:50)

> ### A bio is a claim.
> ### A feed is evidence.
> Nobody checks whether they match.

**Say:** "A profile is a pitch. It's short, it's curated, and there's nothing to
check it against — until you look at what the person actually posts. The gap
between what someone *claims* and what their content *proves* is sitting right
there in public, and no product measures it."

---

## Slide 3 — The insight (0:50–1:20) ← *your thesis*

> ### One profile can't be fake.
> ### Fake is a pattern across accounts.

**Say:** "This is the part that decided the architecture. One person claiming
they're outdoorsy with no outdoor photos is just someone exaggerating. But six
accounts with the same bio reworded, all created within five weeks, all claiming
the same three interests — that's an operation. And you cannot see that by
looking at one profile. You need memory of everything you've checked before, and
you need to search it by *meaning*, because the whole trick is that they reworded
it. That's why Elasticsearch is doing real work here and isn't just where I
parked some JSON."

---

## Slide 4 — How it works (1:20–1:50)

> `Apify` → `Elasticsearch` → `LLM` → `Agent`
> *scrape* · *recall* · *judge* · *ask*

**Say:** "Apify scrapes a public profile — the bio, plus the text of their twelve
most recent posts, in one call. Before anything is saved, Elasticsearch searches
every profile I've already checked and pulls back the ones that *mean* the same
thing. Those go into the prompt as context. The model scores claimed-versus-actual
for each interest, and it has to quote a real caption for every number it gives.
Then the result gets saved into Elasticsearch — so the next profile is compared
against this one. **It gets better at spotting fakes every time I use it.**"

**If asked "what do you mean, twelve posts?"** — "One Apify call returns the bio
and the last twelve captions together. The bio is the claim; those twelve captions
are what they can actually show."

**If asked "what do you mean, the result gets saved?"** — "It's written into
Elasticsearch as a searchable document — the score, the verdict, and the
per-interest breakdown. That's what makes the next profile comparable to this
one. The index is the memory." *(Full document shape is in GLOSSARY.md.)*

---

## Slide 5 — LIVE DEMO (1:50–3:30)

**On screen:** nothing. Full-screen terminal.

You already have **11 real public accounts** indexed — Nike, NatGeo, NASA,
Patagonia, REI, GoPro, Spotify, Sephora, Red Bull, Bon Appétit, Airbnb.

**Beat order — rehearse this exact sequence:**

**1. Audit a public figure or brand whose identity is one specific thing.**
Patagonia claims "in business to save our home planet"; REI says
"#OptOutside" — do their captions actually back that up? Let the claimed-vs-
evidence columns do the talking. **Don't explain the joke.**

**2. Take a handle from the audience. Live scrape.**
This is the moment. Thirty seconds ago the index had never seen that account;
now it has a scored verdict with citations, compared against everything already
in there.

> ⏱ **Timing, measured across 10 real runs: 10 seconds fastest, ~20 seconds
> typical, 37 seconds slowest.** Do **not** promise five seconds. Say "this takes
> about twenty seconds" and then fill the gap — narrate what's happening while it
> runs: "it's fetching the profile and the last twelve posts, then it'll search
> everything I've already indexed for bios that mean the same thing."

**Say before you start:** "This isn't social-media monitoring — I'm not watching
a feed. I'm auditing one profile for self-contradiction."

**Have a screen recording ready.** Live scraping on conference wifi is the single
most likely thing to fail, and 37 seconds of silence feels like five minutes.

---

## Slide 6 — The money query (3:30–4:15)

> ### I never told it what a scam bio looks like.

**Show the real output:**

```
coffee        0.458
crypto        0.458
gym           0.306
faith         0.306
founder       0.306
fitness       0.229
entrepreneur  0.229
espresso      0.229
investor      0.229
```

**Say:** "This is an Elasticsearch aggregation called `significant_text`. I hand
it the accounts flagged as a ring and ask which words are statistically unusual
compared to every other account in the index. It gives me back the fingerprint —
coffee, crypto, gym, faith, founder. **I never wrote that word list.** The
aggregation derived it. There's no language model in this step at all, and there's
no equivalent query in Postgres — you'd have to pull all the data out and write
the statistics yourself."

**Be upfront about where the flagged set came from, in one sentence:** "The
flagged cohort here is seeded demo data — twelve bios built from one template,
and they're labelled `synthetic` in the index so they can't be confused with the
real scraped accounts. The eleven real accounts are the background it's compared
against."

That sentence costs you four seconds and completely defuses "did you make this
up?"

---

## Slide 6b — Creation-date clustering *(optional — cut if short on time)*

```
2026-05-04  ████ 4
2026-05-11  ██ 2
2026-05-18  ██ 2
2026-05-25  ██ 2
2026-06-01  ██ 2
```

⚠️ **Read this before using this slide.** Apify's Instagram scraper does **not**
return account creation dates — only a `joinedRecently` true/false flag. So this
histogram runs on the seeded synthetic records, where the dates are set. It
demonstrates the query, not a finding about real accounts.

**If you show it, say exactly this:** "Farmed accounts cluster in time, organic
ones don't — so a date histogram over the flagged cohort is the natural check.
Being straight with you: Instagram doesn't expose account creation dates, only a
'joined recently' flag. So this runs on the seeded records. To do it on real
accounts I'd need a different signal — earliest post date, which means a deeper
crawl."

**Honestly, cut this slide** unless you have time to spare. Naming a limitation
you can't currently solve is good; spending 30 of 300 seconds on it is not. The
`joinedRecently` flag *is* already used in the scoring — mention that instead if
it comes up.

---

## Slide 7 — Why these tools, and where it goes (4:15–5:00)

> **Apify** — no usable API exists for this
> **Elasticsearch** — the agent's memory
> **LLM** — judgment, with citations
>
> Next: image matching · standing alerts

**Why Apify:** "Instagram's official API only lets you read accounts you own —
there's no endpoint for looking at anyone else's profile. But the public profile
page *is* public; anyone can load it logged out. Apify fetches that same page at
scale and gives me structured data. What it's absorbing is anti-bot handling,
proxy rotation, and pagination — work that has nothing to do with my idea."

**Why Elasticsearch:** "Two reasons. Fake accounts are mass-produced — the same
bio, reworded. Finding 'same meaning, different words' needs semantic search:
Elasticsearch turns each bio into coordinates where similar meanings land near
each other, then finds the nearest ones. Plain keyword search can't do that,
because they deliberately changed the words. But they *do* reuse the exact same
external link — so I need exact matching too, and that's one hybrid query rather
than two systems. And Elasticsearch generates those embeddings itself, so I never
stood up a separate service for it."

**Why an LLM:** "The score needs judgment with a citation. It has to say *why*,
quote a real caption, and refuse to invent evidence when there isn't any.
Structured output keeps the result typed, so the score lands in a field instead
of prose I'd have to parse."

**Close on the roadmap (15 seconds):** "Next is matching *images*, because reused
stolen photos are the real catfish signal. And a percolator — instead of me
querying the fingerprint, I save the fingerprint as a standing query and it alerts
me when a new profile matches it. Monitoring instead of searching."

---

## The honesty line — 10 seconds, don't skip it

> "Public accounts only, and everything you saw was either a public brand account
> or a handle from this room. And a low score means **unverified**, not **proven
> fake** — the tool says 'these claims aren't backed up by the posts,' never
> 'this person is lying.'"

That last distinction is the most defensible thing in the deck. Lead with it if
anyone challenges you on ethics.

---

## Questions you should expect

| Question | Short answer |
|---|---|
| "Why not just use the Instagram API?" | It only reads accounts you own. There is no endpoint for other people's profiles. |
| "Isn't this just social-media monitoring?" | Monitoring watches a feed over time. This audits one profile for self-contradiction and returns a verdict, not a list. |
| "Why not Postgres?" | Finding "same meaning, different words" is vector search. And `significant_text` isn't a query in Postgres — you'd pull the data out and write the statistics yourself. |
| "Where did the flagged accounts come from?" | Seeded synthetic data, labelled `synthetic` in the index. The 11 real accounts are the background corpus. |
| "How do you know the semantic search works?" | Measured: a reworded bio scores **0.84** semantically. Keyword search gives it **0.68**, and its only signal is the shared word "first." |
| "What if the model hallucinates a receipt?" | The prompt forbids inventing evidence and requires a confidence level; thin evidence returns `confidence: low`. It's a real limitation, not a solved problem. |

---

## Design notes

- Dark terminal, one accent colour. No stock hacker photos, no purple gradient.
- One idea per slide. If a slide needs bullets, it's two slides.
- Numbers you can quote from your own build: **11 real accounts indexed**, **12
  captions per call**, **one HTTP request per profile**, **~20 s typical scrape**,
  **0.84 semantic vs 0.68 keyword**, **30 passing tests**.
- **Rehearse twice, out loud, with a timer.** Not in your head.

## Airtable submission

- **Name:** Receipts
- **Repo:** https://github.com/annaa-dev/receipts-catfish-detector
- **One-liner:** Catfish detector — scrapes a public profile with Apify, scores
  claimed vs. actual identity with an LLM, and uses Elasticsearch as the agent's
  memory to catch mass-produced accounts that reword the same bio.
