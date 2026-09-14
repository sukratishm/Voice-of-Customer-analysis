"""Fetch Apple App Store customer reviews, with a disk cache.

Two things this module guarantees, because later phases depend on them:

1. Every successful HTTP response is written to disk before it is parsed. A
   re-run reads from disk and makes zero network calls, so iterating on the
   code downstream costs nothing and does not pester Apple.
2. No single page can kill a run. A page that times out, 500s, or comes back
   as garbage is recorded and stepped over; the rest of the corpus still lands.

Nothing here normalizes anything. This module's job ends at "here are the raw
entries Apple gave us".
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .config import Config

log = logging.getLogger(__name__)

FEED_URL_TEMPLATE = (
    "https://itunes.apple.com/{country}/rss/customerreviews"
    "/page={page}/id={app_id}/sortby=mostrecent/json"
)

SOURCE = "apple_appstore"

# Worth another try: transient server-side or rate-limit responses.
RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})

# A country stops early after this many errors in a row. One bad page is noise;
# three in a row means the storefront is not talking to us right now.
CONSECUTIVE_ERROR_LIMIT = 3


@dataclass
class PageResult:
    """Outcome of asking for one (country, page)."""

    country: str
    page: int
    status: str  # "cached" | "fetched" | "end_of_data" | "error"
    entries: list[dict] = field(default_factory=list)
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status in ("cached", "fetched")


def extract_entries(payload: object) -> list[dict]:
    """Pull the entry list out of a parsed feed payload.

    Handles Apple's quirks: a feed past the last page has no ``entry`` key at
    all, and a feed holding exactly one entry returns an object instead of a
    list. Returns [] rather than raising for anything unexpected — a
    surprising shape is a page to skip, not a run to abort.
    """
    if not isinstance(payload, dict):
        return []
    feed = payload.get("feed")
    if not isinstance(feed, dict):
        return []
    entry = feed.get("entry")
    if entry is None:
        return []
    if isinstance(entry, dict):
        return [entry]
    if isinstance(entry, list):
        return [item for item in entry if isinstance(item, dict)]
    return []


def feed_url(country: str, page: int, app_id: str) -> str:
    return FEED_URL_TEMPLATE.format(country=country, page=page, app_id=app_id)


class AppleReviewFetcher:
    """Paginates a storefront and caches what it gets.

    Cache layout, under ``data/raw/apple/{app_id}/{country}/``:

        page-3.json        the response body exactly as Apple served it
        page-3.meta.json   url, fetch timestamp, status, byte count

    The body is stored verbatim so the cache is a faithful record of what we
    were given, not of what we made of it. The app id is in the path so
    pointing config.toml at a different app cannot read a stale cache.
    """

    def __init__(
        self,
        config: Config,
        raw_dir: Path | None = None,
        force_refresh: bool = False,
        sleep=time.sleep,
    ) -> None:
        self.config = config
        self.app_id = config.app.apple_app_id
        self.apple = config.apple
        self.raw_dir = Path(raw_dir) if raw_dir is not None else config.raw_dir
        self.force_refresh = force_refresh
        self._sleep = sleep
        # Stats, for the run summary.
        self.network_calls = 0
        self.cache_hits = 0
        # Politeness gate. Owned by fetch_page, deliberately not derived from
        # network_calls: that counter lives in the HTTP layer and counts retry
        # attempts, so leaning on it would couple the delay to something that
        # is free to change underneath it.
        self._made_network_request = False

    # -- cache ---------------------------------------------------------------

    def cache_path(self, country: str, page: int) -> Path:
        return self.raw_dir / "apple" / self.app_id / country / f"page-{page}.json"

    def _meta_path(self, country: str, page: int) -> Path:
        return self.cache_path(country, page).with_suffix(".meta.json")

    def _read_cache(self, country: str, page: int) -> dict | None:
        path = self.cache_path(country, page)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            # A corrupt cache entry should not be sticky. Drop it and refetch.
            log.warning("cache entry %s unreadable (%s); will refetch", path, exc)
            return None

    def _write_cache(self, country: str, page: int, body: bytes, url: str) -> None:
        path = self.cache_path(country, page)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        self._meta_path(country, page).write_text(
            json.dumps(
                {
                    "url": url,
                    "fetched_at": datetime.now(timezone.utc).isoformat(),
                    "http_status": 200,
                    "bytes": len(body),
                    "country": country,
                    "page": page,
                    "app_id": self.app_id,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    # -- http ----------------------------------------------------------------

    def _http_get(self, url: str) -> bytes:
        """GET with backoff on transient failures.

        Retries timeouts, connection errors, and retryable status codes with a
        2s/4s/8s backoff. A 403 or 404 is a definitive answer and raises
        immediately — retrying it would just be rude.
        """
        attempt = 0
        while True:
            attempt += 1
            self.network_calls += 1
            try:
                request = Request(
                    url,
                    headers={
                        "User-Agent": self.apple.user_agent,
                        "Accept": "application/json",
                    },
                )
                with urlopen(request, timeout=self.apple.request_timeout_seconds) as response:
                    return response.read()
            except HTTPError as exc:
                retryable = exc.code in RETRYABLE_STATUS
                if not retryable or attempt >= self.apple.max_retries:
                    raise
                backoff = 2**attempt
                log.warning(
                    "HTTP %s from %s (attempt %d/%d); retrying in %ds",
                    exc.code, url, attempt, self.apple.max_retries, backoff,
                )
                self._sleep(backoff)
            except (URLError, TimeoutError, OSError) as exc:
                if attempt >= self.apple.max_retries:
                    raise
                backoff = 2**attempt
                log.warning(
                    "%s fetching %s (attempt %d/%d); retrying in %ds",
                    exc, url, attempt, self.apple.max_retries, backoff,
                )
                self._sleep(backoff)

    # -- fetching ------------------------------------------------------------

    def fetch_page(self, country: str, page: int) -> PageResult:
        """Return one page's raw entries, from cache when possible."""
        url = feed_url(country, page, self.app_id)

        if not self.force_refresh:
            cached = self._read_cache(country, page)
            if cached is not None:
                self.cache_hits += 1
                entries = extract_entries(cached)
                if not entries:
                    return PageResult(country, page, "end_of_data", detail="cached, no entries")
                return PageResult(country, page, "cached", entries)

        # Politeness delay applies only to real network calls, so re-runs and
        # cache hits stay instant. Set before the attempt, not after: a request
        # that failed still hit Apple, so the next one still waits its turn.
        if self._made_network_request:
            self._sleep(self.apple.request_delay_seconds)
        self._made_network_request = True

        try:
            body = self._http_get(url)
        except HTTPError as exc:
            return PageResult(country, page, "error", detail=f"HTTP {exc.code}")
        except (URLError, TimeoutError, OSError) as exc:
            return PageResult(country, page, "error", detail=f"{type(exc).__name__}: {exc}")

        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            # Do not cache unparseable bodies; a re-run should retry them.
            return PageResult(country, page, "error", detail=f"bad JSON: {exc}")

        self._write_cache(country, page, body, url)

        entries = extract_entries(payload)
        if not entries:
            return PageResult(country, page, "end_of_data", detail="no entries")
        return PageResult(country, page, "fetched", entries)

    def fetch_country(self, country: str) -> Iterator[PageResult]:
        """Walk a storefront's pages until it runs out or gives up on us."""
        consecutive_errors = 0
        for page in range(1, self.apple.max_pages + 1):
            result = self.fetch_page(country, page)
            yield result

            if result.status == "end_of_data":
                log.info("%s: no more reviews after page %d", country, page - 1)
                return
            if result.status == "error":
                consecutive_errors += 1
                log.warning("%s page %d failed: %s", country, page, result.detail)
                if consecutive_errors >= CONSECUTIVE_ERROR_LIMIT:
                    log.error(
                        "%s: %d errors in a row, moving on to the next storefront",
                        country, consecutive_errors,
                    )
                    return
                continue

            consecutive_errors = 0
            log.info(
                "%s page %d: %d entries (%s)", country, page, len(result.entries), result.status
            )

    def fetch_all(self, countries: list[str] | None = None) -> Iterator[PageResult]:
        """Walk every configured storefront. One bad storefront is not fatal."""
        for country in countries or list(self.apple.countries):
            log.info("--- storefront: %s ---", country)
            try:
                yield from self.fetch_country(country)
            except Exception as exc:  # noqa: BLE001 - a storefront must not kill the run
                log.exception("storefront %s aborted: %s", country, exc)
