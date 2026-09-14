# Voice-of-Customer Analysis

A portfolio project that turns real app-store reviews into evidence-linked problem
statements a product team could act on.

## Who is working on this

The owner is a **product manager, not a coder**. He reads code and reasons about
architecture but does not write it. Any agent working in this repo should:

- Explain what it is about to do, and why, **before** writing code
- Work in small steps and pause for confirmation rather than dumping a finished system
- State the tradeoff in one line whenever a technical choice had real alternatives
- Give exact terminal commands, including setup, never "just run the tests"

## The product

Pipeline, in order:

1. **Ingest** — pull real reviews, normalize them, freeze them as a stable corpus
2. **Cluster** — group reviews into candidate problem themes
3. **Curate** — a human approves or rejects each theme
4. **Draft** — generate problem statements, each linked back to the reviews that
   support it
5. **Evaluate** — evals over the pipeline, a model router, and cost tracking

## Current phase: 1 — Ingest only

Built and in scope right now:

- Apple App Store public customer-reviews RSS feed (no auth, no API key)
- Multi-country pagination, response caching, normalization, dedupe
- A frozen, committed snapshot corpus

**Explicitly NOT built yet. Do not add these without being asked:**

- Clustering or embeddings
- Any LLM call of any kind
- Any UI
- Evals

`evals/` exists as an empty placeholder for phase 5. Its presence is not an
invitation to fill it.

## Constraints that outlive this phase

**The snapshot must not shift underneath evaluation work.** Phase 5 scores model
output against a fixed corpus. If the corpus changes, historical scores become
meaningless. So snapshots are written to dated filenames and are never edited or
overwritten in place — a re-pull produces a *new* snapshot, and switching to it is a
deliberate, reviewable act.

**Re-runs must be free.** Every HTTP response is cached to disk on first fetch.
Re-running ingest reads from the cache and makes zero network calls. Deleting
`data/raw/` is always safe; it only costs a re-fetch.

**Be polite to Apple.** This is an undocumented public endpoint with no rate-limit
contract. Delay between requests, back off on errors, and never hammer it in a loop.

**Nothing crashes a whole run.** One country or one page failing is normal. It gets
logged and skipped; the other ~900 reviews still land.

## Layout

```
ingest/            Fetch, normalize, snapshot, CLI
data/raw/          Cached HTTP responses — gitignored, disposable
data/snapshots/    Frozen corpora — COMMITTED, treat as immutable
evals/             Placeholder, phase 5
docs/decisions/    One short ADR per decision that had a real alternative
tests/             Fixture-based tests; no network required
```

## Data source notes

Endpoint:

```
https://itunes.apple.com/{country}/rss/customerreviews/page={n}/id={appId}/sortby=mostrecent/json
```

- Up to 50 reviews per page; Apple serves roughly 10 pages, so ~500 per country max
- Current target: Duolingo, App ID `570060128`, countries `us gb ca au in`
- **The first entry in a feed is often app metadata, not a review.** It has no
  `im:rating`. Filter for entries carrying both a rating and a review id.
- When a feed holds a single entry, Apple returns `entry` as an object rather than a
  list. Handle both.
- `created_at` is Apple's `updated` field. Apple does not expose original post time.
  The name is a convenience; it means *last updated*.

## Review schema

Every review normalizes to exactly these fields:

| field | notes |
|---|---|
| `id` | Apple's review id; the dedupe key |
| `source` | `apple_appstore` |
| `country` | lowercase storefront code |
| `rating` | int 1–5 |
| `title` | may be empty |
| `body` | may be empty; real data is messy — emoji, newlines, other languages |
| `app_version` | may be null on older reviews |
| `created_at` | ISO 8601 string, see note above |
| `raw` | the complete original entry, so later phases can recover fields we did not normalize |
