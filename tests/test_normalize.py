"""Normalization and dedupe tests. Fixture-driven, no network."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from ingest.apple_rss import extract_entries
from ingest.normalize import (
    SCHEMA_FIELDS,
    IngestReport,
    Normalizer,
    looks_like_app_metadata,
    normalize_entry,
)

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def entries(name: str) -> list:
    return extract_entries(fixture(name))


def make_entry(**overrides) -> dict:
    """A minimal valid review entry, with fields overridable per test."""
    entry = {
        "im:rating": {"label": "4"},
        "id": {"label": "999"},
        "title": {"label": "A title"},
        "content": {"label": "A body", "attributes": {"type": "text"}},
        "im:version": {"label": "7.24.1"},
        "updated": {"label": "2026-09-13T14:22:01-07:00"},
    }
    for key, value in overrides.items():
        if value is _MISSING:
            entry.pop(key, None)
        else:
            entry[key] = value
    return entry


class _Missing:
    pass


_MISSING = _Missing()


class SchemaTests(unittest.TestCase):
    def test_review_has_exactly_the_nine_schema_fields(self):
        review, reason = normalize_entry(make_entry(), "us")
        self.assertIsNone(reason)
        self.assertEqual(tuple(review.keys()), SCHEMA_FIELDS)
        self.assertEqual(len(SCHEMA_FIELDS), 9)

    def test_fields_carry_the_expected_values(self):
        review, _ = normalize_entry(make_entry(), "gb")
        self.assertEqual(review["id"], "999")
        self.assertEqual(review["source"], "apple_appstore")
        self.assertEqual(review["country"], "gb")
        self.assertEqual(review["rating"], 4)
        self.assertEqual(review["title"], "A title")
        self.assertEqual(review["body"], "A body")
        self.assertEqual(review["app_version"], "7.24.1")
        self.assertEqual(review["created_at"], "2026-09-13T14:22:01-07:00")

    def test_raw_keeps_the_complete_original_entry(self):
        original = make_entry()
        review, _ = normalize_entry(original, "us")
        self.assertEqual(review["raw"], original)
        self.assertIn("im:voteSum", entries("apple_page1_with_metadata.json")[1])


class MetadataEntryTests(unittest.TestCase):
    def test_the_real_metadata_entry_is_detected(self):
        metadata = entries("apple_page1_with_metadata.json")[0]
        self.assertTrue(looks_like_app_metadata(metadata))

    def test_metadata_entry_has_an_id_but_no_rating(self):
        """The reason the filter must check rating, not just id."""
        metadata = entries("apple_page1_with_metadata.json")[0]
        self.assertIn("id", metadata)
        self.assertTrue(metadata["id"]["label"])
        self.assertNotIn("im:rating", metadata)

    def test_metadata_entry_drops_with_its_own_reason(self):
        metadata = entries("apple_page1_with_metadata.json")[0]
        review, reason = normalize_entry(metadata, "us")
        self.assertIsNone(review)
        self.assertEqual(reason, "app_metadata")

    def test_a_real_review_is_not_mistaken_for_metadata(self):
        for entry in entries("apple_page1_with_metadata.json")[1:]:
            with self.subTest(id=entry["id"]["label"]):
                self.assertFalse(looks_like_app_metadata(entry))


class NoSilentCoercionTests(unittest.TestCase):
    def test_rating_out_of_range_is_dropped_not_clamped(self):
        for bad in ("0", "6", "7", "-1", "99"):
            with self.subTest(rating=bad):
                review, reason = normalize_entry(
                    make_entry(**{"im:rating": {"label": bad}}), "us"
                )
                self.assertIsNone(review, f"rating {bad} must not become a review")
                self.assertEqual(reason, "invalid_rating")

    def test_non_numeric_rating_is_dropped_not_defaulted(self):
        for bad in ("", "  ", "four", "4.5", "N/A"):
            with self.subTest(rating=bad):
                review, reason = normalize_entry(
                    make_entry(**{"im:rating": {"label": bad}}), "us"
                )
                self.assertIsNone(review)
                self.assertEqual(reason, "invalid_rating")

    def test_wrongly_typed_rating_is_dropped(self):
        for bad in ({"label": ["5"]}, {"label": {"n": 5}}, "5", 5, None, []):
            with self.subTest(rating=bad):
                review, reason = normalize_entry(
                    make_entry(**{"im:rating": bad}), "us"
                )
                self.assertIsNone(review)
                self.assertIn(reason, ("invalid_rating", "missing_rating"))

    def test_valid_ratings_all_survive(self):
        for good in (1, 2, 3, 4, 5):
            with self.subTest(rating=good):
                review, reason = normalize_entry(
                    make_entry(**{"im:rating": {"label": str(good)}}), "us"
                )
                self.assertIsNone(reason)
                self.assertEqual(review["rating"], good)
                self.assertIsInstance(review["rating"], int)

    def test_missing_rating_is_its_own_reason(self):
        review, reason = normalize_entry(make_entry(**{"im:rating": _MISSING}), "us")
        self.assertIsNone(review)
        self.assertEqual(reason, "missing_rating")

    def test_unusable_id_is_dropped(self):
        for bad in ({"label": ""}, {"label": "   "}, {"label": None}, {}, "123", None):
            with self.subTest(entry_id=bad):
                review, reason = normalize_entry(make_entry(id=bad), "us")
                self.assertIsNone(review)
                self.assertEqual(reason, "missing_id")

    def test_entry_that_is_not_an_object_is_dropped(self):
        for bad in ("a string", 42, None, ["list"]):
            with self.subTest(entry=bad):
                review, reason = normalize_entry(bad, "us")
                self.assertIsNone(review)
                self.assertEqual(reason, "not_a_dict")

    def test_review_with_no_text_at_all_is_dropped(self):
        review, reason = normalize_entry(
            make_entry(title={"label": "  "}, content={"label": ""}), "us"
        )
        self.assertIsNone(review)
        self.assertEqual(reason, "empty_text")

    def test_title_only_review_is_kept(self):
        """Empty body is fine as long as there is some text to analyse."""
        review, reason = normalize_entry(
            make_entry(title={"label": "Crashes"}, content={"label": ""}), "us"
        )
        self.assertIsNone(reason)
        self.assertEqual(review["body"], "")


class AbsenceIsNotCoercionTests(unittest.TestCase):
    """Missing optional fields become None and are counted, not guessed at."""

    def test_missing_app_version_becomes_none_and_keeps_the_review(self):
        review, reason = normalize_entry(make_entry(**{"im:version": _MISSING}), "us")
        self.assertIsNone(reason)
        self.assertIsNone(review["app_version"])

    def test_unparseable_timestamp_becomes_none_rather_than_a_guess(self):
        review, reason = normalize_entry(
            make_entry(updated={"label": "last tuesday"}), "us"
        )
        self.assertIsNone(reason)
        self.assertIsNone(review["created_at"])

    def test_field_gaps_are_counted_on_kept_reviews(self):
        n = Normalizer()
        n.add_entry(make_entry(**{"im:version": _MISSING}), "us")
        n.add_entry(make_entry(id={"label": "1000"}, updated={"label": "nope"}), "us")
        self.assertEqual(n.report.reviews_kept, 2)
        self.assertEqual(n.report.field_gaps["app_version"], 1)
        self.assertEqual(n.report.field_gaps["created_at"], 1)
        self.assertEqual(n.report.total_dropped, 0, "gaps are not drops")


class DedupeTests(unittest.TestCase):
    def test_same_id_twice_in_one_country_keeps_one(self):
        n = Normalizer()
        n.add_entry(make_entry(), "us")
        n.add_entry(make_entry(), "us")
        self.assertEqual(n.report.reviews_kept, 1)
        self.assertEqual(n.report.drops["duplicate_same_country"], 1)

    def test_overlapping_pages_dedupe(self):
        """Fixture page 4 repeats a page 1 review, as real pagination does."""
        n = Normalizer()
        n.add_page(entries("apple_page1_with_metadata.json"), "us")
        before = n.report.reviews_kept
        n.add_page(entries("apple_page4_duplicate.json"), "us")
        self.assertEqual(n.report.drops["duplicate_same_country"], 1)
        self.assertEqual(n.report.reviews_kept, before + 1, "only the new review lands")

    def test_cross_country_duplicate_is_flagged_separately_and_loudly(self):
        n = Normalizer()
        n.add_entry(make_entry(), "us")
        with self.assertLogs("ingest.normalize", level="WARNING"):
            n.add_entry(make_entry(), "gb")

        self.assertEqual(n.report.reviews_kept, 1)
        self.assertEqual(n.report.drops["duplicate_cross_country"], 1)
        self.assertEqual(n.report.drops["duplicate_same_country"], 0)
        self.assertEqual(n.report.cross_country_ids, [("999", "us", "gb")])
        self.assertIn("more than one storefront", n.report.render())

    def test_clean_run_reports_zero_cross_country_duplicates(self):
        n = Normalizer()
        n.add_page(entries("apple_page1_with_metadata.json"), "us")
        n.add_page(entries("apple_page2_single_entry.json"), "gb")
        self.assertEqual(n.report.cross_country_ids, [])
        self.assertIn("Cross-storefront duplicate ids: 0", n.report.render())

    def test_first_occurrence_wins(self):
        n = Normalizer()
        n.add_entry(make_entry(title={"label": "FIRST"}), "us")
        n.add_entry(make_entry(title={"label": "SECOND"}), "us")
        self.assertEqual(n.reviews[0]["title"], "FIRST")

    def test_different_ids_both_kept(self):
        n = Normalizer()
        n.add_entry(make_entry(id={"label": "1"}), "us")
        n.add_entry(make_entry(id={"label": "2"}), "us")
        self.assertEqual(n.report.reviews_kept, 2)


class ReportTests(unittest.TestCase):
    def test_every_entry_is_accounted_for(self):
        """The reconciliation guarantee: seen == kept + dropped, always."""
        n = Normalizer()
        n.add_page(entries("apple_page1_with_metadata.json"), "us")
        n.add_page(entries("apple_page4_duplicate.json"), "us")
        n.add_page(entries("apple_page2_single_entry.json"), "gb")
        n.add_entry(make_entry(**{"im:rating": {"label": "9"}}), "ca")
        n.add_entry("garbage", "ca")

        self.assertTrue(n.report.reconciles(), n.report.render())
        self.assertEqual(
            n.report.entries_seen,
            n.report.reviews_kept + n.report.total_dropped,
        )

    def test_report_names_a_reason_for_every_drop(self):
        n = Normalizer()
        n.add_page(entries("apple_page1_with_metadata.json"), "us")
        n.add_entry(make_entry(**{"im:rating": {"label": "0"}}), "us")
        rendered = n.report.render()
        for reason in n.report.drops:
            self.assertIn(reason, rendered)

    def test_report_keeps_worked_examples_of_each_drop(self):
        n = Normalizer()
        for i in range(10):
            n.add_entry(make_entry(id={"label": str(i)}, **{"im:rating": {"label": "0"}}), "us")
        examples = n.report.drop_examples["invalid_rating"]
        self.assertEqual(len(examples), 3, "capped, not unbounded")
        self.assertIn("rating='0'", examples[0])

    def test_by_country_counts_only_kept_reviews(self):
        n = Normalizer()
        n.add_page(entries("apple_page1_with_metadata.json"), "us")
        self.assertEqual(n.report.by_country["us"], n.report.reviews_kept)

    def test_report_flags_a_failure_to_reconcile(self):
        report = IngestReport(entries_seen=100, reviews_kept=50)
        report.drops["app_metadata"] = 10
        self.assertFalse(report.reconciles())
        self.assertIn("does not reconcile", report.render())

    def test_end_to_end_numbers_on_the_real_fixture_shape(self):
        n = Normalizer()
        n.add_page(entries("apple_page1_with_metadata.json"), "us")
        # 5 entries in: 1 metadata + 4 reviews
        self.assertEqual(n.report.entries_seen, 5)
        self.assertEqual(n.report.reviews_kept, 4)
        self.assertEqual(n.report.drops["app_metadata"], 1)
        self.assertEqual(n.report.field_gaps["app_version"], 1, "one fixture review has no version")


if __name__ == "__main__":
    unittest.main()
