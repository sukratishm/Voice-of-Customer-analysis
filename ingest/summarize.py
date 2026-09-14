"""Human-readable summary of a snapshot.

Deliberately includes app_version coverage up front rather than only in the
drop report: it is the denominator for release-over-release analysis (ADR
0002), so it should be visible every time anyone looks at the corpus.
"""

from __future__ import annotations

import json
import random
import textwrap
from collections import Counter
from pathlib import Path

from .snapshot import app_version_coverage, manifest_for, rating_distribution

DEFAULT_SAMPLE_SIZE = 5
BAR_WIDTH = 40
# Ceiling for a randomly chosen seed, so the printed value stays readable.
SEED_CEILING = 999_999


def _bar(count: int, largest: int, width: int = BAR_WIDTH) -> str:
    if largest <= 0:
        return ""
    return "#" * max(1, round(count / largest * width)) if count else ""


def _pct(part: int, whole: int) -> float:
    return (part / whole * 100) if whole else 0.0


def choose_seed(seed: int | None) -> int:
    """Pick the sampling seed.

    Defaults to a genuinely random seed so repeated runs surface different
    reviews, but the chosen value is always printed so any run can be
    reproduced after the fact -- not just the ones where --seed was remembered
    in advance.
    """
    return seed if seed is not None else random.randrange(SEED_CEILING)


def sample_reviews(reviews: list[dict], count: int, seed: int) -> list[dict]:
    """Pick `count` reviews, reproducibly for a given seed.

    The pool is sorted before sampling. random.sample walks the sequence it is
    handed, so without this a seed would mean different things depending on the
    order the reviews happened to arrive in -- the same seed gives one answer
    when sampling a freshly ingested list and another when sampling the same
    corpus loaded back from its snapshot. Sorting first makes the seed a
    property of the corpus rather than of how it reached us.
    """
    with_text = [review for review in reviews if (review.get("body") or "").strip()]
    pool = sorted(with_text or reviews, key=lambda r: (r.get("country", ""), str(r.get("id", ""))))
    return random.Random(seed).sample(pool, min(count, len(pool)))


def summarize(
    reviews: list[dict],
    snapshot_path: Path | None = None,
    sample_size: int = DEFAULT_SAMPLE_SIZE,
    seed: int | None = None,
    width: int = 88,
) -> str:
    total = len(reviews)
    lines: list[str] = []
    rule = "=" * width

    lines.append(rule)
    if snapshot_path is not None:
        manifest_path = manifest_for(snapshot_path)
        app_name = "?"
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            app_name = manifest.get("source", {}).get("app_name", "?")
            lines.append(f"CORPUS: {app_name}  -  {Path(snapshot_path).name}")
            lines.append(f"Frozen: {manifest.get('created_at', '?')}")
            lines.append(f"sha256: {manifest.get('sha256', '?')}")
        else:
            lines.append(f"CORPUS: {Path(snapshot_path).name}  (no manifest found)")
    else:
        lines.append("CORPUS (in memory, not yet written to a snapshot)")
    lines.append(rule)

    lines.append("")
    lines.append(f"TOTAL REVIEWS: {total}")

    if total == 0:
        lines.append("")
        lines.append("Nothing to summarise.")
        return "\n".join(lines)

    # -- by country ---------------------------------------------------------
    by_country = Counter(review.get("country", "?") for review in reviews)
    largest = max(by_country.values())
    lines.append("")
    lines.append("BY COUNTRY")
    for country, count in by_country.most_common():
        lines.append(
            f"  {country:<4} {count:>6}  {_pct(count, total):>5.1f}%  {_bar(count, largest)}"
        )

    # -- ratings ------------------------------------------------------------
    distribution = rating_distribution(reviews)
    largest_rating = max(distribution.values())
    mean = sum(review["rating"] for review in reviews) / total
    lines.append("")
    lines.append("RATING DISTRIBUTION")
    for star in ("5", "4", "3", "2", "1"):
        count = distribution[star]
        lines.append(
            f"  {star}* {count:>6}  {_pct(count, total):>5.1f}%  "
            f"{_bar(count, largest_rating)}"
        )
    lines.append(f"  mean {mean:.2f}")

    # -- app_version coverage ----------------------------------------------
    coverage = app_version_coverage(reviews)
    lines.append("")
    lines.append("APP_VERSION COVERAGE")
    lines.append(
        f"  {coverage['reviews_with_app_version']} of {total} reviews "
        f"({coverage['coverage_pct']}%) carry an app_version."
    )
    lines.append(
        f"  {coverage['reviews_without_app_version']} do not and are excluded from "
        "release-over-release"
    )
    lines.append(
        "  analysis rather than guessed at (ADR 0002). This is the real denominator"
    )
    lines.append("  for any 'did complaints change when we shipped X' question.")
    versions = Counter(
        review["app_version"] for review in reviews if review.get("app_version")
    )
    if versions:
        top = ", ".join(f"{v} ({c})" for v, c in versions.most_common(5))
        lines.append(f"  {len(versions)} distinct versions; most common: {top}")

    # -- sample -------------------------------------------------------------
    resolved_seed = choose_seed(seed)
    sample = sample_reviews(reviews, sample_size, resolved_seed)
    lines.append("")
    lines.append(rule)
    lines.append(f"{len(sample)} RANDOM REVIEW BODIES  (--seed {resolved_seed} to get these again)")
    lines.append(rule)
    for index, review in enumerate(sample, start=1):
        version = review.get("app_version") or "no version"
        title = review.get("title") or "(no title)"
        lines.append("")
        lines.append(
            f"[{index}] {review.get('rating')}*  {review.get('country')}  "
            f"{version}  id={review.get('id')}"
        )
        lines.append(f"    title: {title}")
        body = (review.get("body") or "").strip() or "(empty body)"
        for paragraph in body.split("\n"):
            if not paragraph.strip():
                lines.append("")
                continue
            lines.extend(
                textwrap.wrap(
                    paragraph,
                    width=width - 4,
                    initial_indent="    ",
                    subsequent_indent="    ",
                )
                or ["    "]
            )
    lines.append("")
    return "\n".join(lines)
