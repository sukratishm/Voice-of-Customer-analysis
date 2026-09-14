#!/usr/bin/env python3
"""THROWAWAY ANALYSIS - not part of the pipeline. Safe to delete.

Answers one question before we design anything: how much of this corpus has
any content to cluster, and does star rating predict it?

Nothing imports this. It only reads a snapshot; it never writes one.

Run:
    python3 scripts/analyze_body_length.py
    python3 scripts/analyze_body_length.py --snapshot data/snapshots/foo.jsonl
    python3 scripts/analyze_body_length.py --threshold 40 --examples 6
"""

from __future__ import annotations

import argparse
import statistics
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ingest.config import load_config  # noqa: E402
from ingest.snapshot import latest_snapshot, load_snapshot  # noqa: E402

BUCKETS = [(0, 0), (1, 19), (20, 39), (40, 79), (80, 159), (160, 319), (320, 10**9)]
THRESHOLDS = (20, 40, 60, 100, 160)
STARS = (1, 2, 3, 4, 5)


def body_of(review: dict) -> str:
    return (review.get("body") or "").strip()


def title_of(review: dict) -> str:
    return (review.get("title") or "").strip()


def combined_of(review: dict) -> str:
    return f"{title_of(review)} {body_of(review)}".strip()


def words(text: str) -> int:
    return len(text.split())


def percentiles(values: list[int]) -> dict[str, float]:
    ordered = sorted(values)
    if not ordered:
        return {}

    def at(fraction: float) -> int:
        index = min(len(ordered) - 1, max(0, round(fraction * (len(ordered) - 1))))
        return ordered[index]

    return {
        "min": ordered[0],
        "p10": at(0.10),
        "p25": at(0.25),
        "median": at(0.50),
        "p75": at(0.75),
        "p90": at(0.90),
        "p95": at(0.95),
        "max": ordered[-1],
        "mean": statistics.mean(ordered),
    }


def pct(part: int, whole: int) -> float:
    return (part / whole * 100) if whole else 0.0


def bar(value: float, width: int = 30) -> str:
    return "#" * max(0, round(value / 100 * width))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", help="path; default is the newest")
    parser.add_argument(
        "--threshold", type=int, default=40, help="the 'content-free' cutoff in characters"
    )
    parser.add_argument("--examples", type=int, default=5, help="sample reviews to print")
    args = parser.parse_args(argv)

    config = load_config()
    path = Path(args.snapshot) if args.snapshot else latest_snapshot(config.snapshot_dir)
    if path is None or not Path(path).exists():
        print("No snapshot found. Run: python3 -m ingest snapshot", file=sys.stderr)
        return 1

    reviews = load_snapshot(path)
    total = len(reviews)
    if total == 0:
        print("Snapshot is empty.", file=sys.stderr)
        return 1

    cut = args.threshold
    lengths = [len(body_of(r)) for r in reviews]
    rule = "=" * 84

    print(rule)
    print(f"BODY LENGTH ANALYSIS  -  {Path(path).name}  -  {total} reviews")
    print(f"'content-free' cutoff: body < {cut} characters")
    print(rule)

    # -- overall ------------------------------------------------------------
    stats = percentiles(lengths)
    print("\nBODY LENGTH IN CHARACTERS, OVERALL")
    print(
        "  min {min}   p10 {p10}   p25 {p25}   median {median}   "
        "p75 {p75}   p90 {p90}   p95 {p95}   max {max}".format(**stats)
    )
    print(f"  mean {stats['mean']:.1f}")

    print("\nDISTRIBUTION")
    for low, high in BUCKETS:
        count = sum(1 for n in lengths if low <= n <= high)
        label = f"{low}" if low == high else (f"{low}+" if high > 10**8 else f"{low}-{high}")
        share = pct(count, total)
        print(f"  {label:>8} chars  {count:>6}  {share:>5.1f}%  {bar(share)}")

    # -- the headline number ------------------------------------------------
    short = sum(1 for n in lengths if n < cut)
    print(f"\nSHARE UNDER {cut} CHARACTERS")
    print(f"  {short} of {total} reviews ({pct(short, total):.1f}%) have a body shorter than {cut} chars.")
    print(f"  {total - short} ({pct(total - short, total):.1f}%) are at or above it.")

    # -- thresholds by rating ----------------------------------------------
    by_star = {star: [r for r in reviews if r.get("rating") == star] for star in STARS}
    print("\nSHARE UNDER EACH THRESHOLD, BY STAR RATING")
    header = "  cutoff   overall  " + "".join(f"{star}*      " for star in STARS)
    print(header)
    for threshold in THRESHOLDS:
        overall = pct(sum(1 for n in lengths if n < threshold), total)
        row = f"  <{threshold:<6} {overall:>6.1f}%  "
        for star in STARS:
            group = by_star[star]
            value = pct(sum(1 for r in group if len(body_of(r)) < threshold), len(group))
            row += f"{value:>5.1f}%  "
        print(row)

    # -- per-rating detail --------------------------------------------------
    print("\nBY STAR RATING")
    print(
        "  rating  count   share   median  mean   median  "
        f"under {cut}   under {cut}"
    )
    print(
        "                          chars   chars  words   "
        "  body      title+body"
    )
    for star in STARS:
        group = by_star[star]
        if not group:
            print(f"  {star}*           0")
            continue
        body_lengths = [len(body_of(r)) for r in group]
        word_counts = [words(body_of(r)) for r in group]
        short_body = sum(1 for r in group if len(body_of(r)) < cut)
        short_combined = sum(1 for r in group if len(combined_of(r)) < cut)
        print(
            f"  {star}*      {len(group):>6}  {pct(len(group), total):>5.1f}%  "
            f"{statistics.median(body_lengths):>6.0f}  "
            f"{statistics.mean(body_lengths):>5.0f}  "
            f"{statistics.median(word_counts):>5.0f}   "
            f"{pct(short_body, len(group)):>6.1f}%   "
            f"{pct(short_combined, len(group)):>6.1f}%"
        )

    # -- the specific question ---------------------------------------------
    positive = [r for r in reviews if r.get("rating") in (4, 5)]
    negative = [r for r in reviews if r.get("rating") in (1, 2)]
    neutral = [r for r in reviews if r.get("rating") == 3]

    print("\n" + rule)
    print("4-5 STAR vs 1-2 STAR  -  does rating predict whether there is anything to cluster?")
    print(rule)
    for name, group in (("4-5 star", positive), ("3 star", neutral), ("1-2 star", negative)):
        if not group:
            continue
        body_lengths = [len(body_of(r)) for r in group]
        short_count = sum(1 for n in body_lengths if n < cut)
        stats = percentiles(body_lengths)
        print(f"\n  {name}: {len(group)} reviews ({pct(len(group), total):.1f}% of corpus)")
        print(
            f"    median {stats['median']} chars, mean {stats['mean']:.0f}, "
            f"p90 {stats['p90']}, max {stats['max']}"
        )
        print(
            f"    under {cut} chars: {short_count} ({pct(short_count, len(group)):.1f}%)  {bar(pct(short_count, len(group)))}"
        )
        usable = len(group) - short_count
        print(f"    at or above {cut}: {usable} ({pct(usable, len(group)):.1f}%)")

    # -- how much survives a naive gate ------------------------------------
    survivors = [r for r in reviews if len(body_of(r)) >= cut]
    print("\n" + rule)
    print(f"IF WE GATED ON body >= {cut} CHARS")
    print(rule)
    print(f"  Corpus drops from {total} to {len(survivors)} ({pct(len(survivors), total):.1f}% retained)")
    survivor_stars = Counter(r.get("rating") for r in survivors)
    print("  Surviving mix by rating:")
    for star in STARS:
        kept = survivor_stars.get(star, 0)
        original = len(by_star[star])
        print(
            f"    {star}*  {kept:>6} of {original:<6} "
            f"({pct(kept, original):>5.1f}% of that rating survives, "
            f"{pct(kept, len(survivors)):>5.1f}% of the gated corpus)"
        )

    # -- does the title rescue anything? -----------------------------------
    rescued = [
        r for r in reviews if len(body_of(r)) < cut and len(combined_of(r)) >= cut
    ]
    print(f"\n  Reviews with a short body but a title that pushes them over {cut}: {len(rescued)}")
    print("  (gate on body alone and these are lost; worth eyeballing before deciding)")
    for review in rescued[: args.examples]:
        print(f"    {review['rating']}* title={title_of(review)!r}  body={body_of(review)!r}")

    # -- calibration examples ----------------------------------------------
    print("\n" + rule)
    print("WHAT THE CUTOFF ACTUALLY LOOKS LIKE")
    print(rule)
    just_under = sorted(
        [r for r in reviews if 0 < len(body_of(r)) < cut],
        key=lambda r: -len(body_of(r)),
    )[: args.examples]
    just_over = sorted(
        [r for r in reviews if len(body_of(r)) >= cut], key=lambda r: len(body_of(r))
    )[: args.examples]
    print(f"\n  LONGEST reviews still below {cut} chars (these would be dropped):")
    for review in just_under:
        print(f"    {review['rating']}* [{len(body_of(review)):>3}] {body_of(review)!r}")
    print(f"\n  SHORTEST reviews at or above {cut} chars (these would be kept):")
    for review in just_over:
        print(f"    {review['rating']}* [{len(body_of(review)):>3}] {body_of(review)!r}")

    empty = sum(1 for n in lengths if n == 0)
    if empty:
        print(f"\n  Note: {empty} reviews have a completely empty body ({pct(empty, total):.1f}%).")
    print("")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
