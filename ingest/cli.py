"""Command line entry point.  Run as: python3 -m ingest <command>"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .apple_rss import AppleReviewFetcher
from .config import ConfigError, load_config
from .normalize import Normalizer
from .snapshot import (
    SnapshotExists,
    latest_snapshot,
    load_snapshot,
    verify_snapshot,
    write_snapshot,
)
from .summarize import DEFAULT_SAMPLE_SIZE, summarize


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)-7s %(message)s",
        stream=sys.stderr,
    )


def _fetch_into(normalizer: Normalizer | None, args, config) -> tuple[AppleReviewFetcher, list]:
    fetcher = AppleReviewFetcher(config, force_refresh=args.force_refresh)
    countries = args.country or list(config.apple.countries)
    results = []
    for result in fetcher.fetch_all(countries):
        results.append(result)
        if normalizer is not None and result.ok:
            normalizer.add_page(result.entries, result.country)
    return fetcher, results


def _print_fetch_stats(fetcher: AppleReviewFetcher, results: list) -> None:
    ok = sum(1 for r in results if r.ok)
    errors = [r for r in results if r.status == "error"]
    print("")
    print(f"Pages read:     {len(results)} ({ok} with reviews)")
    print(f"Network calls:  {fetcher.network_calls}")
    print(f"Cache hits:     {fetcher.cache_hits}")
    if errors:
        print(f"Pages failed:   {len(errors)}")
        for result in errors:
            print(f"  {result.country} page {result.page}: {result.detail}")


def cmd_fetch(args, config) -> int:
    """Warm the cache. No normalization, no snapshot."""
    fetcher, results = _fetch_into(None, args, config)
    entries = sum(len(r.entries) for r in results)
    _print_fetch_stats(fetcher, results)
    print(f"Raw entries:    {entries}")
    print(f"Cached under:   {config.raw_dir}")
    return 0


def cmd_snapshot(args, config) -> int:
    """Fetch (from cache where possible), normalize, dedupe, freeze."""
    normalizer = Normalizer()
    fetcher, results = _fetch_into(normalizer, args, config)
    _print_fetch_stats(fetcher, results)

    print("")
    print("-" * 88)
    print("INGEST REPORT")
    print("-" * 88)
    print(normalizer.report.render())

    if not normalizer.reviews:
        print("")
        print("No reviews survived normalization - not writing a snapshot.")
        return 1

    try:
        snapshot_path, manifest_path, written = write_snapshot(
            normalizer.reviews, config, normalizer.report, force=args.force
        )
    except SnapshotExists as exc:
        print("")
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2

    print("")
    if written:
        print(f"Snapshot: {snapshot_path}")
        print(f"Manifest: {manifest_path}")
    else:
        print(f"Snapshot unchanged, nothing rewritten: {snapshot_path}")

    if not args.no_summary:
        print("")
        print(summarize(normalizer.reviews, snapshot_path, args.samples, args.seed))
    return 0


def _resolve_snapshot(args, config) -> Path | None:
    if args.snapshot:
        return Path(args.snapshot)
    return latest_snapshot(config.snapshot_dir)


def cmd_summarize(args, config) -> int:
    path = _resolve_snapshot(args, config)
    if path is None:
        print(
            f"No snapshot found in {config.snapshot_dir}. "
            "Run: python3 -m ingest snapshot",
            file=sys.stderr,
        )
        return 1
    print(summarize(load_snapshot(path), path, args.samples, args.seed))
    return 0


def cmd_verify(args, config) -> int:
    path = _resolve_snapshot(args, config)
    if path is None:
        print(f"No snapshot found in {config.snapshot_dir}.", file=sys.stderr)
        return 1
    ok, message = verify_snapshot(path)
    print(message if ok else f"FAILED: {message}", file=sys.stdout if ok else sys.stderr)
    return 0 if ok else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m ingest",
        description="Pull App Store reviews and freeze them as a stable corpus.",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="debug logging")
    parser.add_argument("--config", help="path to config.toml")
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_fetch_flags(subparser):
        subparser.add_argument(
            "--country",
            action="append",
            help="limit to this storefront; repeatable. Default: all in config.toml",
        )
        subparser.add_argument(
            "--force-refresh",
            action="store_true",
            help="ignore the cache and refetch from Apple",
        )

    def add_sample_flags(subparser):
        subparser.add_argument(
            "--samples",
            type=int,
            default=DEFAULT_SAMPLE_SIZE,
            help=f"how many random review bodies to show (default {DEFAULT_SAMPLE_SIZE})",
        )
        subparser.add_argument(
            "--seed",
            type=int,
            default=None,
            help="seed for the random sample. Default: a fresh random seed each run, "
            "printed in the output so any run can be reproduced afterwards.",
        )

    fetch = subparsers.add_parser("fetch", help="warm the cache only")
    add_fetch_flags(fetch)
    fetch.set_defaults(func=cmd_fetch)

    snapshot = subparsers.add_parser(
        "snapshot", help="fetch, normalize, dedupe, and freeze a corpus"
    )
    add_fetch_flags(snapshot)
    add_sample_flags(snapshot)
    snapshot.add_argument(
        "--force", action="store_true", help="overwrite a differing same-day snapshot"
    )
    snapshot.add_argument(
        "--no-summary", action="store_true", help="skip the summary at the end"
    )
    snapshot.set_defaults(func=cmd_snapshot)

    summarize_cmd = subparsers.add_parser("summarize", help="summarize a snapshot")
    summarize_cmd.add_argument("--snapshot", help="path; default is the newest")
    add_sample_flags(summarize_cmd)
    summarize_cmd.set_defaults(func=cmd_summarize)

    verify = subparsers.add_parser(
        "verify", help="check a snapshot against its manifest checksum"
    )
    verify.add_argument("--snapshot", help="path; default is the newest")
    verify.set_defaults(func=cmd_verify)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _setup_logging(args.verbose)
    try:
        config = load_config(Path(args.config) if args.config else None)
    except ConfigError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 2
    return args.func(args, config)


if __name__ == "__main__":
    raise SystemExit(main())
