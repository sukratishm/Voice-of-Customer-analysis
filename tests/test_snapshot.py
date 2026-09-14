"""Snapshot immutability, checksums, and summary tests."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from ingest.config import load_config
from ingest.normalize import Normalizer
from ingest.snapshot import (
    SnapshotExists,
    app_version_coverage,
    latest_snapshot,
    load_snapshot,
    manifest_for,
    serialize,
    sha256_of,
    snapshot_stem,
    verify_snapshot,
    write_snapshot,
)
from ingest.summarize import choose_seed, sample_reviews, summarize

WHEN = datetime(2026, 9, 14, tzinfo=timezone.utc)


def review(rid, country="us", rating=4, version="7.24.1", body="a body", title="a title"):
    return {
        "id": str(rid),
        "source": "apple_appstore",
        "country": country,
        "rating": rating,
        "title": title,
        "body": body,
        "app_version": version,
        "created_at": "2026-09-13T14:22:01-07:00",
        "raw": {"id": {"label": str(rid)}},
    }


class SnapshotTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.config = load_config()

    def write(self, reviews, **kwargs):
        kwargs.setdefault("when", WHEN)
        return write_snapshot(reviews, self.config, snapshot_dir=self.dir, **kwargs)


class DeterminismTests(SnapshotTestCase):
    def test_row_order_does_not_depend_on_input_order(self):
        a = [review(3, "gb"), review(1, "us"), review(2, "ca")]
        b = [review(2, "ca"), review(3, "gb"), review(1, "us")]
        self.assertEqual(serialize(a), serialize(b))

    def test_same_data_produces_the_same_checksum(self):
        reviews = [review(1), review(2)]
        self.assertEqual(
            sha256_of(serialize(reviews)), sha256_of(serialize(list(reversed(reviews))))
        )

    def test_one_review_per_line(self):
        reviews = [review(i) for i in range(7)]
        self.assertEqual(len(serialize(reviews).strip().splitlines()), 7)

    def test_non_ascii_survives_the_round_trip(self):
        original = [review(1, body="es buena pero muy cara \U0001f615 éàü")]
        path, _, _ = self.write(original)
        self.assertEqual(load_snapshot(path)[0]["body"], original[0]["body"])

    def test_schema_field_order_is_preserved_on_disk(self):
        path, _, _ = self.write([review(1)])
        first_line = path.read_text(encoding="utf-8").splitlines()[0]
        self.assertTrue(first_line.startswith('{"id":'))


class ImmutabilityTests(SnapshotTestCase):
    def test_filename_is_dated_and_slugged(self):
        self.assertEqual(snapshot_stem(self.config, WHEN), "duolingo-apple-20260914")

    def test_identical_rewrite_is_a_no_op_not_an_error(self):
        reviews = [review(1), review(2)]
        path, _, first = self.write(reviews)
        _, _, second = self.write(reviews)
        self.assertTrue(first)
        self.assertFalse(second, "no rewrite needed when nothing changed")

    def test_differing_same_day_rewrite_is_refused(self):
        self.write([review(1)])
        with self.assertRaises(SnapshotExists) as caught:
            self.write([review(1), review(2)])
        self.assertIn("--force", str(caught.exception))

    def test_refusal_leaves_the_original_untouched(self):
        path, _, _ = self.write([review(1)])
        before = path.read_text(encoding="utf-8")
        with self.assertRaises(SnapshotExists):
            self.write([review(1), review(2)])
        self.assertEqual(path.read_text(encoding="utf-8"), before)

    def test_force_overwrites_deliberately(self):
        path, _, _ = self.write([review(1)])
        _, _, written = self.write([review(1), review(2)], force=True)
        self.assertTrue(written)
        self.assertEqual(len(load_snapshot(path)), 2)

    def test_a_later_date_gets_its_own_file(self):
        first, _, _ = self.write([review(1)])
        later, _, _ = self.write(
            [review(1), review(2)], when=datetime(2026, 9, 20, tzinfo=timezone.utc)
        )
        self.assertNotEqual(first, later)
        self.assertTrue(first.exists(), "the old corpus survives untouched")
        self.assertEqual(len(load_snapshot(first)), 1)


class ManifestTests(SnapshotTestCase):
    def manifest(self, reviews, report=None):
        _, manifest_path, _ = self.write(reviews, report=report)
        return json.loads(manifest_path.read_text(encoding="utf-8"))

    def test_manifest_records_counts_and_checksum(self):
        data = self.manifest([review(1, "us"), review(2, "gb")])
        self.assertEqual(data["review_count"], 2)
        self.assertEqual(data["by_country"], {"us": 1, "gb": 1})
        self.assertEqual(len(data["sha256"]), 64)

    def test_manifest_records_rating_distribution_with_all_five_keys(self):
        data = self.manifest([review(1, rating=5), review(2, rating=5), review(3, rating=1)])
        self.assertEqual(data["rating_distribution"], {"1": 1, "2": 0, "3": 0, "4": 0, "5": 2})

    def test_manifest_records_app_version_coverage(self):
        data = self.manifest([review(1), review(2, version=None), review(3)])
        coverage = data["app_version_coverage"]
        self.assertEqual(coverage["reviews_with_app_version"], 2)
        self.assertEqual(coverage["reviews_without_app_version"], 1)
        self.assertAlmostEqual(coverage["coverage_pct"], 66.7)

    def test_manifest_carries_the_ingest_report(self):
        normalizer = Normalizer()
        normalizer.add_entry({"im:name": {"label": "App"}}, "us")
        normalizer.add_entry(
            {"im:rating": {"label": "4"}, "id": {"label": "1"}, "title": {"label": "t"}}, "us"
        )
        data = self.manifest(normalizer.reviews, report=normalizer.report)
        report = data["ingest_report"]
        self.assertEqual(report["entries_seen"], 2)
        self.assertEqual(report["reviews_kept"], 1)
        self.assertEqual(report["drops"]["app_metadata"], 1)
        self.assertTrue(report["reconciles"])

    def test_manifest_warns_about_created_at(self):
        data = self.manifest([review(1)])
        self.assertIn("ADR 0002", data["notes"]["created_at_field"])

    def test_manifest_records_which_app_and_storefronts(self):
        data = self.manifest([review(1)])
        self.assertEqual(data["source"]["apple_app_id"], self.config.app.apple_app_id)
        self.assertEqual(data["source"]["countries"], list(self.config.apple.countries))


class VerifyTests(SnapshotTestCase):
    def test_a_fresh_snapshot_verifies(self):
        path, _, _ = self.write([review(1), review(2)])
        ok, message = verify_snapshot(path)
        self.assertTrue(ok, message)

    def test_a_tampered_snapshot_fails_verification(self):
        path, _, _ = self.write([review(1), review(2)])
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(review(99)) + "\n")
        ok, message = verify_snapshot(path)
        self.assertFalse(ok)
        self.assertIn("mismatch", message)

    def test_a_missing_manifest_fails_verification(self):
        path, manifest_path, _ = self.write([review(1)])
        manifest_path.unlink()
        ok, message = verify_snapshot(path)
        self.assertFalse(ok)
        self.assertIn("missing manifest", message)

    def test_manifest_path_derives_from_the_snapshot_path(self):
        self.assertEqual(
            manifest_for(Path("/x/duolingo-apple-20260914.jsonl")).name,
            "duolingo-apple-20260914.manifest.json",
        )

    def test_latest_snapshot_picks_the_newest_date(self):
        self.write([review(1)], when=datetime(2026, 9, 1, tzinfo=timezone.utc))
        newest, _, _ = self.write([review(2)], when=datetime(2026, 9, 20, tzinfo=timezone.utc))
        self.write([review(3)], when=datetime(2026, 9, 10, tzinfo=timezone.utc))
        self.assertEqual(latest_snapshot(self.dir), newest)

    def test_latest_snapshot_is_none_when_empty(self):
        self.assertIsNone(latest_snapshot(self.dir))


class SamplingTests(unittest.TestCase):
    def setUp(self):
        self.reviews = [review(i, body=f"body number {i}") for i in range(50)]

    def test_same_seed_gives_the_same_sample(self):
        self.assertEqual(
            [r["id"] for r in sample_reviews(self.reviews, 5, 42)],
            [r["id"] for r in sample_reviews(self.reviews, 5, 42)],
        )

    def test_different_seeds_give_different_samples(self):
        a = [r["id"] for r in sample_reviews(self.reviews, 5, 1)]
        b = [r["id"] for r in sample_reviews(self.reviews, 5, 2)]
        self.assertNotEqual(a, b)

    def test_default_seed_is_random_but_explicit_seed_is_honoured(self):
        self.assertEqual(choose_seed(7), 7)
        seeds = {choose_seed(None) for _ in range(30)}
        self.assertGreater(len(seeds), 1, "default must vary between runs")

    def test_sample_prefers_reviews_that_have_a_body(self):
        reviews = [review(1, body=""), review(2, body=""), review(3, body="real text")]
        sample = sample_reviews(reviews, 1, seed=0)
        self.assertEqual(sample[0]["id"], "3")

    def test_same_seed_survives_a_different_input_order(self):
        """A seed must identify a set of reviews, not an arrival order.

        `snapshot` samples the freshly ingested list; `summarize` samples the
        same corpus read back from disk in sorted order. Both must agree.
        """
        shuffled = list(reversed(self.reviews))
        self.assertEqual(
            [r["id"] for r in sample_reviews(self.reviews, 5, 99)],
            [r["id"] for r in sample_reviews(shuffled, 5, 99)],
        )

    def test_sampling_does_not_exceed_the_corpus(self):
        self.assertEqual(len(sample_reviews(self.reviews[:2], 5, 1)), 2)

    def test_seed_used_is_printed_so_a_random_run_can_be_reproduced(self):
        output = summarize(self.reviews, seed=None)
        self.assertIn("--seed ", output)


class SummaryTests(unittest.TestCase):
    def setUp(self):
        self.reviews = (
            [review(i, "us", rating=5) for i in range(10)]
            + [review(100 + i, "gb", rating=1, version=None) for i in range(5)]
            + [review(200 + i, "in", rating=3) for i in range(5)]
        )

    def test_summary_reports_the_total(self):
        self.assertIn("TOTAL REVIEWS: 20", summarize(self.reviews, seed=1))

    def test_summary_breaks_down_by_country(self):
        output = summarize(self.reviews, seed=1)
        self.assertIn("BY COUNTRY", output)
        for country in ("us", "gb", "in"):
            self.assertIn(country, output)

    def test_summary_shows_every_rating_bucket_including_zeroes(self):
        output = summarize(self.reviews, seed=1)
        self.assertIn("RATING DISTRIBUTION", output)
        for star in ("1*", "2*", "3*", "4*", "5*"):
            self.assertIn(star, output)

    def test_summary_surfaces_app_version_coverage_prominently(self):
        output = summarize(self.reviews, seed=1)
        self.assertIn("APP_VERSION COVERAGE", output)
        self.assertIn("75.0%", output, "15 of 20 reviews carry a version")
        self.assertIn("ADR 0002", output)

    def test_summary_shows_the_requested_number_of_bodies(self):
        output = summarize(self.reviews, sample_size=5, seed=1)
        for index in range(1, 6):
            self.assertIn(f"[{index}]", output)

    def test_empty_corpus_does_not_crash(self):
        self.assertIn("Nothing to summarise", summarize([], seed=1))


if __name__ == "__main__":
    unittest.main()
