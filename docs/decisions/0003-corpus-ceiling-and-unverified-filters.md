# 0003 — What the first real pull measured, and what it did not

- **Status:** Accepted (record of measurement)
- **Date:** 2026-09-14
- **Phase:** 1 (Ingest)

First run against the live Apple feed: 2500 reviews, 5 storefronts, 0 dropped.
This ADR records what that run settled, what it left open, and one claim that
looks settled but is not.

## Settled: review ids are globally unique

Zero cross-storefront duplicate ids across 2500 reviews. The dedupe design
(key on id alone, first occurrence wins) rested on inference until this run;
it now rests on measurement. Detection stays in the code as a regression guard.

Zero same-storefront duplicates either — with `sortby=mostrecent`, Apple's pages
did not overlap at all. The dedupe path is therefore **untested against real
overlapping data**, though fixtures cover it.

## NOT settled: 2500 is not a proven ceiling

It is tempting to read "500 from every storefront" as hitting Apple's cap. It is
not, and the distinction matters because corpus size bounds everything
downstream.

The run read 50 pages: 5 storefronts x `max_pages = 10` from `config.toml`.
**Every one of those 50 pages returned a full 50 reviews, and no storefront ever
reported `end_of_data`.** The run stopped because it ran out of *configured*
pages, not because Apple ran out of reviews.

So what was measured is our own ceiling. Apple's real limit is widely believed
to be ~10 pages, and probably is, but this corpus is not evidence of it.

**The test is cheap** — raise `max_pages` to 12 and re-run. Pages 1-10 are
already cached and cost nothing; only pages 11 and 12 hit the network, 10 new
requests total. If page 11 comes back empty, the cap is real and 2500 is the
ceiling. If it returns reviews, the corpus can grow without adding storefronts.

Until someone runs it, "2500 is the maximum" is an assumption, not a finding.

## NOT verified: the app-metadata filter never fired

`drops: 0` means **zero** entries were rejected as app metadata. The
first-entry-is-app-metadata gotcha did not occur once across 50 real pages.

Possibilities, in rough order of likelihood:

1. The quirk belongs to the **XML** flavour of this feed, not the JSON one we
   use, and has been mistakenly carried into JSON advice.
2. Apple changed the JSON response since the gotcha was first documented.
3. It is storefront- or app-specific and this combination does not trigger it.

**Keep the filter.** It costs one dictionary lookup per entry, it is covered by
fixtures, and a false negative (a metadata entry entering the corpus as a
review with no rating) would be far more annoying to debug than the check is to
carry. But it is now **documented as unverified against production data** rather
than assumed to be load-bearing.

Do not treat its presence as evidence that the quirk is real. If a future run
ever reports a non-zero `app_metadata` drop count, that is the first real
sighting and worth noting here.

## Also unexercised by real data

Because the run was clean, these paths have fixture coverage only:

- `invalid_rating`, `missing_rating`, `missing_id`, `empty_text` — all zero
- `not_a_dict` — zero
- Page-level error handling, retries, backoff — no page failed
- `end_of_data` — never reached, per the ceiling question above

A clean first run is good news about Apple's feed quality. It is not evidence
that our error handling works in production; only fixtures say that.
