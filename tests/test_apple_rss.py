"""Fetcher tests. No network: every test either reads the cache or stubs HTTP."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from urllib.error import HTTPError, URLError

from ingest.apple_rss import (
    CONSECUTIVE_ERROR_LIMIT,
    AppleReviewFetcher,
    extract_entries,
    feed_url,
)
from ingest.config import load_config

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def fixture_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


class ExtractEntriesTests(unittest.TestCase):
    def test_list_of_entries(self):
        entries = extract_entries(fixture("apple_page1_with_metadata.json"))
        self.assertEqual(len(entries), 5, "1 metadata entry + 4 reviews")

    def test_single_entry_returned_as_object(self):
        """Apple's quirk: one entry comes back as a dict, not a list of one."""
        entries = extract_entries(fixture("apple_page2_single_entry.json"))
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["id"]["label"], "11234567894")

    def test_feed_past_last_page_has_no_entry_key(self):
        self.assertEqual(extract_entries(fixture("apple_page3_empty.json")), [])

    def test_garbage_shapes_return_empty_rather_than_raising(self):
        for payload in (None, [], "nope", {}, {"feed": None}, {"feed": {"entry": 7}}):
            with self.subTest(payload=payload):
                self.assertEqual(extract_entries(payload), [])


class FeedUrlTests(unittest.TestCase):
    def test_matches_the_documented_endpoint(self):
        self.assertEqual(
            feed_url("gb", 3, "570060128"),
            "https://itunes.apple.com/gb/rss/customerreviews"
            "/page=3/id=570060128/sortby=mostrecent/json",
        )


class FetcherTestCase(unittest.TestCase):
    """Shared setup: a fetcher pointed at a temp cache dir with sleep disabled."""

    def setUp(self):
        import tempfile

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.slept = []
        self.fetcher = AppleReviewFetcher(
            load_config(),
            raw_dir=Path(self.tmp.name),
            sleep=self.slept.append,
        )

    def seed_cache(self, country: str, page: int, fixture_name: str) -> Path:
        path = self.fetcher.cache_path(country, page)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(fixture_bytes(fixture_name))
        return path

    def stub_http(self, responder):
        """Replace the HTTP layer. Tests never reach the network."""
        self.fetcher._http_get = responder


class CacheTests(FetcherTestCase):
    def test_cache_hit_makes_no_network_call_and_no_delay(self):
        self.seed_cache("us", 1, "apple_page1_with_metadata.json")
        self.stub_http(lambda url: self.fail("should not have hit the network"))

        result = self.fetcher.fetch_page("us", 1)

        self.assertEqual(result.status, "cached")
        self.assertEqual(len(result.entries), 5)
        self.assertEqual(self.fetcher.network_calls, 0)
        self.assertEqual(self.slept, [], "cache hits must not sleep; re-runs stay instant")

    def test_response_body_is_cached_verbatim(self):
        body = fixture_bytes("apple_page1_with_metadata.json")
        self.stub_http(lambda url: body)

        result = self.fetcher.fetch_page("us", 1)

        self.assertEqual(result.status, "fetched")
        cached = self.fetcher.cache_path("us", 1)
        self.assertTrue(cached.exists())
        self.assertEqual(cached.read_bytes(), body, "cache must be byte-identical to the response")

    def test_cache_writes_a_meta_sidecar(self):
        self.stub_http(lambda url: fixture_bytes("apple_page1_with_metadata.json"))
        self.fetcher.fetch_page("gb", 2)

        meta = json.loads(
            self.fetcher._meta_path("gb", 2).read_text(encoding="utf-8")
        )
        self.assertEqual(meta["country"], "gb")
        self.assertEqual(meta["page"], 2)
        self.assertEqual(meta["http_status"], 200)
        self.assertIn("fetched_at", meta)
        self.assertEqual(meta["url"], feed_url("gb", 2, self.fetcher.app_id))

    def test_second_run_reads_cache_instead_of_refetching(self):
        calls = []

        def responder(url):
            calls.append(url)
            return fixture_bytes("apple_page1_with_metadata.json")

        self.stub_http(responder)
        first = self.fetcher.fetch_page("us", 1)
        second = self.fetcher.fetch_page("us", 1)

        self.assertEqual(first.status, "fetched")
        self.assertEqual(second.status, "cached")
        self.assertEqual(len(calls), 1, "the re-run must cost zero network calls")

    def test_cache_is_keyed_by_app_id(self):
        """Repointing config at another app must not read the old app's cache."""
        path = self.fetcher.cache_path("us", 1)
        self.assertIn(self.fetcher.app_id, path.parts)

    def test_corrupt_cache_entry_is_refetched_not_fatal(self):
        path = self.seed_cache("us", 1, "apple_page1_with_metadata.json")
        path.write_text("{ this is not json", encoding="utf-8")
        self.stub_http(lambda url: fixture_bytes("apple_page1_with_metadata.json"))

        with self.assertLogs("ingest.apple_rss", level="WARNING"):
            result = self.fetcher.fetch_page("us", 1)

        self.assertEqual(result.status, "fetched")


class FailureTests(FetcherTestCase):
    def test_http_error_returns_an_error_result_rather_than_raising(self):
        def responder(url):
            raise HTTPError(url, 503, "Service Unavailable", {}, None)

        self.stub_http(responder)
        result = self.fetcher.fetch_page("us", 1)

        self.assertEqual(result.status, "error")
        self.assertIn("503", result.detail)
        self.assertFalse(result.ok)

    def test_network_error_returns_an_error_result(self):
        self.stub_http(lambda url: (_ for _ in ()).throw(URLError("no route to host")))
        result = self.fetcher.fetch_page("us", 1)
        self.assertEqual(result.status, "error")

    def test_unparseable_body_is_an_error_and_is_not_cached(self):
        self.stub_http(lambda url: b"<html>maintenance</html>")
        result = self.fetcher.fetch_page("us", 1)

        self.assertEqual(result.status, "error")
        self.assertFalse(
            self.fetcher.cache_path("us", 1).exists(),
            "a bad body must not be cached, or the re-run would never retry it",
        )

    def test_empty_page_reports_end_of_data_not_error(self):
        self.stub_http(lambda url: fixture_bytes("apple_page3_empty.json"))
        result = self.fetcher.fetch_page("us", 9)
        self.assertEqual(result.status, "end_of_data")


class PaginationTests(FetcherTestCase):
    def test_country_stops_when_pages_run_out(self):
        def responder(url):
            page = int(url.split("page=")[1].split("/")[0])
            if page == 1:
                return fixture_bytes("apple_page1_with_metadata.json")
            if page == 2:
                return fixture_bytes("apple_page2_single_entry.json")
            return fixture_bytes("apple_page3_empty.json")

        self.stub_http(responder)
        results = list(self.fetcher.fetch_country("us"))

        self.assertEqual([r.page for r in results], [1, 2, 3])
        self.assertEqual(results[-1].status, "end_of_data")

    def test_one_bad_page_does_not_end_the_country(self):
        def responder(url):
            page = int(url.split("page=")[1].split("/")[0])
            if page == 2:
                raise HTTPError(url, 500, "boom", {}, None)
            if page >= 4:
                return fixture_bytes("apple_page3_empty.json")
            return fixture_bytes("apple_page1_with_metadata.json")

        self.stub_http(responder)
        results = list(self.fetcher.fetch_country("us"))
        statuses = [(r.page, r.status) for r in results]

        self.assertIn((2, "error"), statuses)
        self.assertIn((3, "fetched"), statuses, "must keep going past a single bad page")

    def test_country_gives_up_after_consecutive_errors(self):
        self.stub_http(
            lambda url: (_ for _ in ()).throw(HTTPError(url, 500, "boom", {}, None))
        )
        results = list(self.fetcher.fetch_country("us"))

        self.assertEqual(len(results), CONSECUTIVE_ERROR_LIMIT)
        self.assertTrue(all(r.status == "error" for r in results))

    def test_fetch_all_covers_every_configured_storefront(self):
        seen = []

        def responder(url):
            seen.append(url.split("itunes.apple.com/")[1].split("/")[0])
            return fixture_bytes("apple_page3_empty.json")

        self.stub_http(responder)
        list(self.fetcher.fetch_all())

        self.assertEqual(seen, list(self.fetcher.apple.countries))

    def test_a_storefront_blowing_up_does_not_kill_the_run(self):
        def responder(url):
            if "/gb/" in url:
                raise RuntimeError("unexpected explosion")
            return fixture_bytes("apple_page3_empty.json")

        self.stub_http(responder)
        with self.assertLogs("ingest.apple_rss", level="ERROR"):
            results = list(self.fetcher.fetch_all(["gb", "ca"]))

        self.assertTrue(any(r.country == "ca" for r in results), "ca must still be attempted")


class PolitenessTests(FetcherTestCase):
    def test_delay_between_real_network_calls(self):
        self.stub_http(lambda url: fixture_bytes("apple_page1_with_metadata.json"))
        self.fetcher.fetch_page("us", 1)
        self.fetcher.fetch_page("us", 2)

        self.assertEqual(
            self.slept,
            [self.fetcher.apple.request_delay_seconds],
            "one delay, before the second network call",
        )

    def test_a_failed_request_still_counts_toward_politeness(self):
        """A request that errored still hit Apple, so the next one still waits."""
        calls = {"n": 0}

        def responder(url):
            calls["n"] += 1
            if calls["n"] == 1:
                raise HTTPError(url, 500, "boom", {}, None)
            return fixture_bytes("apple_page1_with_metadata.json")

        self.stub_http(responder)
        self.fetcher.fetch_page("us", 1)
        self.fetcher.fetch_page("us", 2)

        self.assertEqual(self.slept, [self.fetcher.apple.request_delay_seconds])


if __name__ == "__main__":
    unittest.main()
