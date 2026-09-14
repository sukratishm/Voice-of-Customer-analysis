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

## Measured on the first real pull (2026-09-14)

`app_version` coverage came back at **100%** — all 2500 reviews carry one. The
worry that drove the "exclude nulls rather than impute" rule did not
materialise on this corpus, and the denominator is the whole corpus.

**But the spread is heavily concentrated, which bounds what the proxy can do.**
22 distinct versions appear, and roughly 1900 of 2500 reviews (~76%) sit in just
three consecutive builds:

| version | reviews |
|---|---|
| 7.139.0 | 869 |
| 7.137.0 | 531 |
| 7.138.0 | 493 |
| 7.136.0 | 128 |
| 7.135.2 | 79 |

The remaining ~17 versions share a long, thin tail.

This is expected — the feed is sorted `mostrecent`, so it samples whatever was
current — but it has a direct consequence:

**Release-over-release analysis is viable across roughly the four or five most
recent builds only. It cannot support long-range trends.** A chart comparing
7.139.0 against 7.137.0 rests on hundreds of reviews per side and is sound. A
chart reaching back to 7.79.0 rests on single digits and is noise wearing the
costume of a trend.

Anything below a floor of ~50 reviews for a version should not be plotted as a
point. The tail is for spot-checking individual reviews, not for comparison.

Note this is a *different* limitation from the `created_at` problem above.
`created_at` is unsound at any scale; `app_version` is sound but has a short
usable range. One is a correctness problem, the other a sample-size problem.

## Consequences

- Phase 4 "trending" analysis is keyed on app version, and its x-axis is versions, not
  dates.
- Any chart with a date axis needs an explicit justification, because the default
  answer is that we cannot draw one.
- `app_version` coverage becomes a corpus quality metric worth reporting in the
  ingest summary.
