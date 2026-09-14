# 0001 — Single source (Apple App Store) for the MVP

- **Status:** Accepted, deferred for revisit
- **Date:** 2026-09-14
- **Phase:** 1 (Ingest)

## Context

The pipeline needs a review corpus large enough to cluster into meaningful themes —
target was 1000+ reviews. Apple's public customer-reviews RSS feed serves roughly 500
reviews per country storefront, so pulling five storefronts clears that bar from a
single source with no auth, no API key, and no vendor account.

Google Play is the obvious second source. It has no comparable public feed; reaching
it means either a third-party scraping library or the Google Play Developer API, which
requires a service account and ownership of the app. Neither is free in effort terms,
and the scraping route adds a dependency whose breakage would be inherited by every
later phase.

## Decision

Ingest from **Apple App Store only** for the MVP. Build the normalized schema with a
`source` field from day one, so a second source is an addition rather than a
refactor, but do not build one now.

## What we gain

- One fetch path, one response shape, one set of quirks to handle
- No scraping dependency and no vendor credentials in a portfolio project
- 1000+ reviews reached immediately, which is what clustering actually needs
- Ingest stays small enough to be fully understood, which matters more in phase 1
  than breadth does

## What we give up

- **We cannot separate platform-specific complaints from product-wide ones.** If
  iOS users complain about a crash on a specific iOS version, this corpus cannot tell
  us whether that is an iOS bug or a Duolingo bug — there is no Android population to
  compare against. Any theme the pipeline surfaces is, strictly, an *iOS user* theme.
- Apple's reviewer population skews by device, region, and price point relative to the
  full user base. Themes are representative of App Store reviewers, not of all users.
- Android-only regressions are invisible.

## Framing

This is a **deferred decision, not planned work.** Nothing in the roadmap commits to
adding Google Play. The cost of revisiting is bounded because `source` already exists
in the schema and snapshots are per-corpus, so a second source means a new normalizer
and a new snapshot — not a change to anything downstream.

The trigger to revisit would be a concrete question this corpus cannot answer, e.g.
"is this crash platform-specific?" — not a general wish for more data.

## Consequences for downstream phases

Problem statements drafted in phase 4 should be scoped honestly. "iOS users report X"
is supportable from this corpus; "users report X" is not.
