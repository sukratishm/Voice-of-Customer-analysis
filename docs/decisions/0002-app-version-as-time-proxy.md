# 0002 — Use `app_version` as the time proxy, not `created_at`

- **Status:** Accepted
- **Date:** 2026-09-14
- **Phase:** 1 (Ingest)

## Context

The normalized schema carries a `created_at` field. The name is misleading and the
underlying data does not support what the name implies.

Apple's customer-reviews feed exposes a single timestamp per review, `updated`. There
is no original-post timestamp anywhere in the feed. A review written in 2019 and
edited by its author last week reports last week's date, and nothing in the payload
distinguishes it from a genuinely new review.

This matters because the obvious analysis to build in phase 4 is "complaints about X
are trending up since release Y." Built on `created_at`, that chart would silently
misdate an unknown fraction of the corpus, in a way that produces a plausible-looking
result rather than an obviously broken one. Wrong-but-believable is the expensive
failure mode.

We also cannot measure the size of the error: Apple gives us no edit flag, so we
cannot even report what fraction of the corpus is affected.

## Decision

Keep `created_at` in the schema — it is the only timestamp we have and it is
genuinely useful for coarse recency and for sorting — but treat it as **unsafe for
any time-series or trend analysis**, and document that at the schema itself rather
than in a footnote.

For release-over-release analysis, use **`app_version`** as the time proxy:

- It is recorded per review by Apple at submission time
- It does not change when a review is edited
- App versions are ordered, which is what trend analysis actually needs
- It answers the more useful product question directly: did complaints about X change
  when we shipped 7.24?

Reviews with a null `app_version` (Apple omits it on some older reviews) are
**excluded** from release-over-release analysis rather than imputed or bucketed into
"unknown-then-guessed." An honest smaller denominator beats a complete but invented
one.

## Alternatives rejected

- **Use `created_at` anyway and caveat it in the UI.** Caveats do not survive
  screenshots. The chart would outlive the disclaimer.
- **Drop `created_at` entirely.** Overcorrects — it is fine for "show me recent
  reviews" and for sorting, and discarding source data we already have is wasteful.
- **Infer creation date from `app_version` release dates.** Adds a second data source
  (version release history), and still cannot handle edits. More machinery, same
  uncertainty.

## Consequences

- Phase 4 "trending" analysis is keyed on app version, and its x-axis is versions, not
  dates.
- Any chart with a date axis needs an explicit justification, because the default
  answer is that we cannot draw one.
- `app_version` coverage becomes a corpus quality metric worth reporting in the
  ingest summary.
