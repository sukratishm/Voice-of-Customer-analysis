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

**Shape of the source: one app, several storefronts.** The pipeline pulls the same
app's reviews from multiple country storefronts and merges them into one corpus.
Which app and which storefronts live in `config.toml`, not in code and not here —
the app under analysis is expected to change.

Apple's public customer-reviews RSS feed, no auth and no API key:

```
https://itunes.apple.com/{country}/rss/customerreviews/page={n}/id={appId}/sortby=mostrecent/json
```

- Up to 50 reviews per page; Apple serves roughly 10 pages, so ~500 per storefront max
- **The first entry in a feed is often app metadata, not a review.** It has no
  `im:rating`. Filter for entries carrying both a rating and a review id.
- When a feed holds a single entry, Apple returns `entry` as an object rather than a
  list. Handle both.
- A feed past the last page returns valid JSON with no `entry` key at all. That is
  the normal end-of-data signal, not an error.
- Apple can return 403 to a request with no `User-Agent`. Always send one.

### Measured against the live feed, 2026-09-14

First real pull: 2500 reviews, 500 from each of us/gb/ca/au/in, zero dropped.

- **Review ids are globally unique** — zero cross-storefront duplicates across
  2500 reviews. Dedupe on id alone is now measured, not inferred.
- **No duplicates at all**, even within a storefront. With `sortby=mostrecent`,
  the ten pages did not overlap.
- **`app_version` coverage was 100%**, but concentrated: ~76% of the corpus sits
  in three consecutive builds. Release analysis is viable across ~4-5 recent
  versions only — see ADR 0002.
- **The app-metadata first entry never appeared.** The filter is kept but is
  *unverified against production data* — it may be an XML-feed quirk. See ADR
  0003.
- **2500 is NOT a proven ceiling.** The run stopped at `max_pages = 10`, not at
  a refusal from Apple; every page returned a full 50 and no storefront reported
  end-of-data. Raising `max_pages` to 12 tests it for ~10 extra requests. See
  ADR 0003.

Single-source for the MVP is a deliberate, revisitable choice — see
`docs/decisions/0001-single-source-mvp.md`.

## Review schema

Every review normalizes to exactly these fields:

| field | notes |
|---|---|
| `id` | Apple's review id; the dedupe key |
| `source` | `apple_appstore` |
| `country` | lowercase storefront code |
| `rating` | int 1-5 |
| `title` | may be empty |
| `body` | may be empty; real data is messy - emoji, newlines, other languages |
| `app_version` | may be null on older reviews; **this is the time proxy, see below** |
| `created_at` | ISO 8601 string; **not safe for trend analysis, see below** |
| `raw` | the complete original entry, so later phases can recover fields we did not normalize |

### `created_at` is not a creation time

Apple's feed exposes only an `updated` timestamp. A review written in 2019 and edited
last week reports last week. `created_at` is therefore **unsafe for any
"complaints over time" or "trending since release" analysis** - such a chart would
silently misdate an unknown fraction of the corpus.

Use `app_version` as the time proxy for release-over-release analysis instead. It is
recorded per review, it does not change after the fact, and app versions are ordered.
Reviews with a null `app_version` are excluded from that analysis rather than guessed
at. See `docs/decisions/0002-app-version-as-time-proxy.md`.
