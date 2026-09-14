# 0004 — The signal gate is classification, not length

- **Status:** Accepted
- **Date:** 2026-09-14
- **Phase:** 1 measurement, informing phase 2
- **Supersedes:** the length-gate approach assumed in earlier sessions

## This ADR records a reversal

Two sessions ago the project owner asked for a length-based gate to strip
content-free reviews before clustering. It sounded obviously right: short review,
nothing to cluster.

The data killed it. This ADR exists to record *why the idea was rejected by its
own author*, so nobody re-proposes it in six months on the same intuition.

The measurement came first and the decision second. That order is the point.

## What was measured

Corpus: `duolingo-apple-20260914.jsonl`, 2500 reviews, sha256 `5b93a6b6...`.

Body length overall: median 106 chars, p10 19, p90 418, max 4615.

| cutoff | share of corpus below it |
|---|---|
| < 20 chars | 10.2% |
| < 40 chars | 20.7% |
| < 60 chars | 31.7% |
| < 100 chars | 47.4% |

## Finding 1 — length is not a negativity filter

The original worry was that a length gate would preferentially keep angry
reviews and bias every downstream theme toward the negative. It does not.

| rating | survives a 40-char gate | share of gated corpus |
|---|---|---|
| 1* | 85.9% | 20.9% (from 19.3%) |
| 2* | 93.1% | 6.8% (from 5.8%) |
| 3* | 94.6% | 10.6% (from 8.9%) |
| 4* | 90.2% | 15.3% (from 13.4%) |
| 5* | 70.0% | 46.4% (from 52.6%) |

The concern is cleared. More precisely: this is not a negativity filter, it is a
**short-effusive-praise filter**. 5-star is the only rating with a survival rate
materially below the rest; every other rating survives at 86-95%. The corpus mix
shifts by about six points, not by a landslide.

## Finding 2 — but length does not separate signal from sentiment

A 40-char gate drops only 20.7% of the corpus, which is a weak cost lever, and
what it drops is not what we wanted dropped. Real examples from this corpus:

**Killed by a 40-char gate, both carry a specific product claim:**

- `Is good but you loose energy to quickly` (38 chars) — a claim about the
  energy/hearts economy
- `Please add urdu, sign language, punjabi` (38 chars) — three specific feature
  requests

**Survives a 40-char gate, carries nothing:**

- `The best language learning app everrrrrr` (40 chars)

Character count measures *long versus short*. It does not measure *informative
versus empty*. Those are different axes, and the gate we need is on the second.

## Finding 3 — the title carries content the body lacks

Measured after the fact, and it changes the design of even the cheap pre-filter.
193 reviews have a body under 40 chars but a title that pushes the pair past it,
e.g. title `Results after 303 day streak` with body `Pretty good to be honest`.

Comparing candidate pre-filters, where "signal-word review" is a review whose
text matches a crude product-vocabulary regex (ads, crash, streak, price,
subscription, hearts, lesson, …):

| pre-filter | dropped | % of corpus | signal-word reviews lost |
|---|---|---|---|
| `body < 20` | 255 | 10.2% | 12 |
| **`title+body < 20`** | **111** | **4.4%** | **1** |
| `title+body < 30` | 212 | 8.5% | 5 |
| `body < 40` (rejected) | 518 | 20.7% | 42 |

Measuring the title and body together instead of the body alone cuts signal loss
**twelve-fold** — 12 reviews to 1 — while also dropping less of the corpus.
Body-only length is simply the wrong measurement, at any threshold.

The single signal-word review lost to `title+body < 20` is `Good` / `Too
expensive`. One review in 2500 is an acceptable price.

Caveat on the method: the regex is a crude proxy for "contains a specific
product claim", not ground truth. It misses claims phrased without those words
and false-positives on phrases like `Best language`. These numbers are
directional, not exact.

## Finding 4 — 3-star and 2-star are the densest signal in the corpus

The most useful result, and counter to the intuition that 1-star reviews are
where the complaints live.

| rating | count | share | median chars | median words | under 40 chars |
|---|---|---|---|---|---|
| 1* | 482 | 19.3% | 158 | 30 | 14.1% |
| **2*** | **145** | **5.8%** | **233** | **45** | **6.9%** |
| **3*** | **222** | **8.9%** | **189** | **36** | **5.4%** |
| 4* | 336 | 13.4% | 147 | 28 | 9.8% |
| 5* | 1315 | 52.6% | 69 | 14 | 30.0% |

2-star reviews are the longest in the corpus, and 3-star reviews are the least
likely to be content-free — only 5.4% fall under 40 chars, better than any other
rating.

The plausible reading: a 1-star reviewer is often venting, and venting is short
(`This is a scam`). A 2- or 3-star reviewer is doing cost-benefit reasoning —
"I like X but Y is broken" — which is structurally the same shape as an
actionable problem statement. Ambivalence produces detail; anger produces volume.

Note the corpus is thin exactly where it is richest: 2- and 3-star together are
only 14.7% of reviews, 367 in total.

## Decisions

1. **No 40-character gate.** Rejected on the evidence above, by the person who
   asked for it.

2. **A cheap length pre-filter is retained, but measured on `title + body`, not
   `body` alone.** Provisional threshold `title+body < 20`, dropping 4.4%.

3. **Classification is the actual gate.** The remaining corpus is classified on
   a single question — *does this review contain a specific product claim?* —
   and that decision, not length, determines what reaches clustering.

4. **The golden set over-samples 2- and 3-star relative to their corpus share,**
   because that is where classification is hardest and where the signal is
   densest.

## The pre-filter is not really a cost lever

Worth stating plainly, because it changes how the threshold should be tuned.
Classifying ~2390 reviews with a small model costs cents. Trimming 4.4% of that
saves a rounding error.

So if the pre-filter earns its place, it is not on cost — it is on keeping
obvious junk out of the downstream set and out of the eval. That means it should
be tuned for **precision, never recall**: it must never drop a review carrying a
claim, and it may freely leave junk for the classifier to catch. When in doubt,
let it through. A pre-filter that saves money by discarding signal is strictly
worse than no pre-filter.

## Stratify the golden set; never reweight the corpus

These are two different sets and conflating them would quietly corrupt phase 4.

- **The golden set** is for measuring classifier accuracy. Over-sampling 2- and
  3-star is correct there: you want hard, ambiguous cases, not a representative
  sample.
- **The production corpus** is what theme frequency is computed from. It must
  stay representative. If 2- and 3-star reviews were over-weighted there, every
  theme's apparent prevalence would be wrong, and "how many users hit this"
  is the number a PM acts on.

Same reviews, opposite sampling rules, depending on whether you are measuring
*the classifier* or *the users*.

## Consequences

- Phase 2 needs a classifier before it needs a clusterer.
- The classifier's prompt targets one binary question, not a taxonomy.
- Whatever "specific product claim" means must be written down as labelling
  guidance before the golden set is built, or two labellers will disagree and
  the eval will measure the disagreement.
- Titles are part of the input to classification, not just to the pre-filter.
