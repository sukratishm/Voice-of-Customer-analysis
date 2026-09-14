"""Turn Apple's feed entries into the 9-field review schema.

Two rules govern this module:

1. **Nothing is dropped silently.** Every entry that does not become a review
   is counted under a named reason, with a few worked examples kept for
   eyeballing. A run that takes in 1200 entries and emits 1050 can account for
   the other 150 without anyone re-reading this file.

2. **Nothing is coerced.** A rating that is not an integer 1-5, or a field of
   an unexpected type, drops the review and logs the offending value. We never
   guess a replacement. A smaller honest corpus beats a complete invented one
   (see docs/decisions/0002).

Field-level absence is distinct from bad data. Apple legitimately omits
`im:version` on older reviews, and ADR 0002 already says `created_at` is not
load-bearing. Those become None, are counted as coverage gaps, and do NOT drop
the review — recording an absence is not the same as inventing a value.
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime

log = logging.getLogger(__name__)

SOURCE = "apple_appstore"

SCHEMA_FIELDS = (
    "id",
    "source",
    "country",
    "rating",
    "title",
    "body",
    "app_version",
    "created_at",
    "raw",
)

# Keys that only ever appear on the app-metadata entry Apple sometimes puts
# first. Used to name the drop reason accurately, not to gate the filter --
# the gate is "has a rating and an id", per spec.
APP_METADATA_KEYS = ("im:name", "im:price", "im:releaseDate", "im:artist", "im:image")

# Human-readable explanation per drop reason, so the report explains itself.
DROP_REASONS = {
    "not_a_dict": "feed entry was not an object",
    "app_metadata": "app metadata entry, not a review (no rating)",
    "missing_rating": "no im:rating field",
    "invalid_rating": "rating was not an integer 1-5",
    "missing_id": "no usable review id",
    "empty_text": "title and body were both empty - nothing to analyse",
    "duplicate_same_country": "review id already seen in the same storefront",
    "duplicate_cross_country": "review id already seen in a DIFFERENT storefront",
}

MAX_EXAMPLES_PER_REASON = 3


def _label(value: object) -> str | None:
    """Unwrap Apple's ``{"label": ...}`` shape.

    Returns None for anything else. Never coerces a dict or list into a string.
    """
    if not isinstance(value, dict):
        return None
    label = value.get("label")
    if isinstance(label, str):
        return label
    if isinstance(label, int) and not isinstance(label, bool):
        return str(label)
    if isinstance(label, float):
        return str(label)
    return None


def looks_like_app_metadata(entry: dict) -> bool:
    """True for the app-info entry Apple sometimes puts first in a feed.

    It carries no rating, and its ``id`` holds a store URL plus an ``im:id``
    attribute rather than a bare review id.
    """
    if "im:rating" in entry:
        return False
    if any(key in entry for key in APP_METADATA_KEYS):
        return True
    entry_id = entry.get("id")
    return isinstance(entry_id, dict) and bool(entry_id.get("attributes"))


def _parse_rating(raw: object) -> int | None:
    """Apple's rating as an int 1-5, or None if it is anything else.

    No rounding, no clamping, no defaulting. A rating of "4.5", "" or 7 is not
    a rating we can use, and pretending otherwise would put invented data in
    the corpus.
    """
    label = _label(raw)
    if label is None:
        return None
    try:
        rating = int(label.strip())
    except (ValueError, AttributeError):
        return None
    if not 1 <= rating <= 5:
        return None
    return rating


def _validated_timestamp(raw: object) -> str | None:
    """Return Apple's timestamp string if it parses as ISO 8601, else None.

    The string is returned unchanged rather than reformatted -- the cache holds
    what Apple sent and so should the snapshot. See ADR 0002: this field is not
    safe for trend analysis regardless.
    """
    label = _label(raw)
    if label is None or not label.strip():
        return None
    try:
        datetime.fromisoformat(label.strip())
    except ValueError:
        return None
    return label.strip()


def _clean_text(raw: object) -> str:
    label = _label(raw)
    return label.strip() if label else ""


@dataclass
class IngestReport:
    """Where every entry went. Printed at the end of a run."""

    entries_seen: int = 0
    reviews_kept: int = 0
    drops: Counter = field(default_factory=Counter)
    drop_examples: dict[str, list[str]] = field(default_factory=dict)
    by_country: Counter = field(default_factory=Counter)
    # Kept reviews missing an optional field. Not drops -- coverage gaps.
    field_gaps: Counter = field(default_factory=Counter)
    # Populated only if a review id turns up under two storefronts, which we
    # expect never to happen. See the dedupe note in normalize().
    cross_country_ids: list[tuple[str, str, str]] = field(default_factory=list)

    @property
    def total_dropped(self) -> int:
        return sum(self.drops.values())

    def record_drop(self, reason: str, example: str) -> None:
        self.drops[reason] += 1
        bucket = self.drop_examples.setdefault(reason, [])
        if len(bucket) < MAX_EXAMPLES_PER_REASON:
            bucket.append(example)

    def reconciles(self) -> bool:
        """Every entry seen must be either kept or dropped. No leakage."""
        return self.entries_seen == self.reviews_kept + self.total_dropped

    def render(self) -> str:
        lines: list[str] = []
        lines.append("Entries in:  %d" % self.entries_seen)
        lines.append("Reviews out: %d" % self.reviews_kept)
        lines.append("Dropped:     %d" % self.total_dropped)

        if self.drops:
            lines.append("")
            lines.append("Where the dropped entries went:")
            width = max(len(r) for r in self.drops)
            for reason, count in self.drops.most_common():
                explanation = DROP_REASONS.get(reason, "")
                lines.append(f"  {reason:<{width}}  {count:>6}   {explanation}")
                for example in self.drop_examples.get(reason, []):
                    lines.append(f"  {'':<{width}}          e.g. {example}")

        if self.field_gaps:
            lines.append("")
            lines.append("Coverage gaps in KEPT reviews (field absent, review retained):")
            width = max(len(f) for f in self.field_gaps)
            for name, count in self.field_gaps.most_common():
                pct = (count / self.reviews_kept * 100) if self.reviews_kept else 0.0
                lines.append(f"  {name:<{width}}  {count:>6}  ({pct:.1f}% of kept)")

        lines.append("")
        if self.cross_country_ids:
            lines.append(
                "WARNING: %d review id(s) appeared in more than one storefront."
                % len(self.cross_country_ids)
            )
            lines.append(
                "This was expected to be impossible. Investigate before trusting"
            )
            lines.append("country-level analysis. First few:")
            for review_id, first, second in self.cross_country_ids[:5]:
                lines.append(f"  id {review_id}: first seen in {first}, again in {second}")
        else:
            lines.append(
                "Cross-storefront duplicate ids: 0 "
                "(confirms review ids are globally unique)"
            )

        if not self.reconciles():
            lines.append("")
            lines.append(
                "ERROR: report does not reconcile - %d seen != %d kept + %d dropped"
                % (self.entries_seen, self.reviews_kept, self.total_dropped)
            )
        return "\n".join(lines)


def normalize_entry(entry: object, country: str) -> tuple[dict | None, str | None]:
    """One feed entry -> (review, None) or (None, drop_reason).

    Pure: no state, no dedupe. Dedupe lives in Normalizer because it needs to
    remember what it has seen.
    """
    if not isinstance(entry, dict):
        return None, "not_a_dict"

    if looks_like_app_metadata(entry):
        return None, "app_metadata"

    if "im:rating" not in entry:
        return None, "missing_rating"

    rating = _parse_rating(entry.get("im:rating"))
    if rating is None:
        log.warning(
            "dropping review in %s: unusable rating %r",
            country, entry.get("im:rating"),
        )
        return None, "invalid_rating"

    review_id = _label(entry.get("id"))
    if review_id is None or not review_id.strip():
        log.warning("dropping review in %s: unusable id %r", country, entry.get("id"))
        return None, "missing_id"
    review_id = review_id.strip()

    title = _clean_text(entry.get("title"))
    body = _clean_text(entry.get("content"))
    if not title and not body:
        return None, "empty_text"

    review = {
        "id": review_id,
        "source": SOURCE,
        "country": country,
        "rating": rating,
        "title": title,
        "body": body,
        "app_version": _clean_text(entry.get("im:version")) or None,
        "created_at": _validated_timestamp(entry.get("updated")),
        "raw": entry,
    }
    return review, None


class Normalizer:
    """Accumulates reviews across storefronts, dedupes, and reports.

    Dedupe key is the review id alone, keeping the first occurrence.

    Why id alone rather than (id, country): Apple's review ids are a single
    global space and a review belongs to exactly one storefront, so a collision
    across countries is impossible.

    MEASURED, not inferred. First real pull, 2026-09-14: 2500 reviews across
    us/gb/ca/au/in produced zero cross-storefront duplicate ids. The detection
    below is kept anyway -- it costs nothing, it guards against Apple changing
    the id scheme, and it is what turned this from an assumption into a fact.

    Had cross-country duplicates existed, keying on (id, country) would have
    double-counted the review and inflated whatever theme it belongs to; since
    theme frequency is the product, over-counting is the worse failure.

    Also measured on that run: zero same-country duplicates either. With
    sortby=mostrecent, Apple's ten pages did not overlap at all.
    """

    def __init__(self) -> None:
        self.report = IngestReport()
        self.reviews: list[dict] = []
        self._first_seen_country: dict[str, str] = {}

    def add_entry(self, entry: object, country: str) -> dict | None:
        self.report.entries_seen += 1

        review, reason = normalize_entry(entry, country)
        if reason is not None:
            self.report.record_drop(reason, _describe(entry, country))
            return None

        assert review is not None
        review_id = review["id"]
        previous_country = self._first_seen_country.get(review_id)
        if previous_country is not None:
            if previous_country == country:
                self.report.record_drop(
                    "duplicate_same_country", f"{country} id={review_id}"
                )
            else:
                self.report.cross_country_ids.append(
                    (review_id, previous_country, country)
                )
                log.warning(
                    "review id %s seen in %s and again in %s - expected impossible",
                    review_id, previous_country, country,
                )
                self.report.record_drop(
                    "duplicate_cross_country",
                    f"id={review_id} in {previous_country} and {country}",
                )
            return None

        self._first_seen_country[review_id] = country
        self.reviews.append(review)
        self.report.reviews_kept += 1
        self.report.by_country[country] += 1
        if review["app_version"] is None:
            self.report.field_gaps["app_version"] += 1
        if review["created_at"] is None:
            self.report.field_gaps["created_at"] += 1
        return review

    def add_page(self, entries: list, country: str) -> None:
        for entry in entries:
            self.add_entry(entry, country)


def _describe(entry: object, country: str) -> str:
    """A short, safe identifier for a dropped entry, for the report."""
    if not isinstance(entry, dict):
        return f"{country} {type(entry).__name__}"
    entry_id = _label(entry.get("id")) or "<no id>"
    rating = _label(entry.get("im:rating"))
    title = _clean_text(entry.get("title"))
    bits = [f"{country} id={entry_id[:60]}"]
    if rating is not None:
        bits.append(f"rating={rating!r}")
    if title:
        bits.append(f"title={title[:40]!r}")
    return " ".join(bits)
