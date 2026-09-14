"""Write and read the frozen corpus.

A snapshot is the contract with phase 5. Evals score model output against a
fixed set of reviews; if that set shifts, historical scores stop meaning
anything. So:

- Snapshots are written to dated filenames and never edited in place.
- A same-day rewrite that would change the contents is refused unless forced.
- Every snapshot ships a manifest with a sha256, so "is this the corpus I
  scored against?" is a question with a real answer.
- Rows are sorted deterministically, so identical data produces identical
  bytes and therefore an identical checksum.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from .config import Config
from .normalize import IngestReport

SNAPSHOT_SUFFIX = ".jsonl"
MANIFEST_SUFFIX = ".manifest.json"


class SnapshotExists(Exception):
    """Raised rather than overwriting a snapshot that differs from the new one."""


def snapshot_stem(config: Config, when: datetime | None = None) -> str:
    when = when or datetime.now(timezone.utc)
    return f"{config.app.slug}-apple-{when:%Y%m%d}"


def sort_key(review: dict) -> tuple[str, str]:
    """Deterministic row order.

    Sorted by storefront then id rather than by recency: `created_at` is a
    last-updated timestamp (ADR 0002), so ordering by it would be both
    unstable and meaningless. Determinism is what buys us a stable checksum.
    """
    return (review.get("country", ""), str(review.get("id", "")))


def serialize(reviews: list[dict]) -> str:
    """Reviews as JSONL. One review per line, UTF-8, schema field order kept."""
    lines = [
        json.dumps(review, ensure_ascii=False, sort_keys=False)
        for review in sorted(reviews, key=sort_key)
    ]
    return "\n".join(lines) + "\n" if lines else ""


def sha256_of(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def rating_distribution(reviews: list[dict]) -> dict[str, int]:
    counts = Counter(review["rating"] for review in reviews)
    return {str(star): counts.get(star, 0) for star in (1, 2, 3, 4, 5)}


def app_version_coverage(reviews: list[dict]) -> dict:
    """How much of the corpus can take part in release-over-release analysis.

    Per ADR 0002 `app_version` is the time proxy, and reviews without one are
    excluded from that analysis rather than guessed at. This number is
    therefore the real denominator, so it is recorded in the manifest and
    surfaced in every summary.
    """
    total = len(reviews)
    with_version = sum(1 for review in reviews if review.get("app_version"))
    return {
        "reviews_with_app_version": with_version,
        "reviews_without_app_version": total - with_version,
        "coverage_pct": round(with_version / total * 100, 1) if total else 0.0,
    }


def build_manifest(
    reviews: list[dict],
    config: Config,
    report: IngestReport | None,
    snapshot_name: str,
    checksum: str,
    byte_count: int,
) -> dict:
    manifest = {
        "snapshot": snapshot_name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "review_count": len(reviews),
        "sha256": checksum,
        "bytes": byte_count,
        "schema_version": 1,
        "source": {
            "app_name": config.app.name,
            "app_slug": config.app.slug,
            "apple_app_id": config.app.apple_app_id,
            "countries": list(config.apple.countries),
            "max_pages": config.apple.max_pages,
        },
        "by_country": dict(Counter(review["country"] for review in reviews)),
        "rating_distribution": rating_distribution(reviews),
        "app_version_coverage": app_version_coverage(reviews),
        "notes": {
            "created_at_field": (
                "Apple exposes only a last-updated timestamp. Not safe for trend "
                "analysis - use app_version as the time proxy. See ADR 0002."
            ),
            "row_order": "sorted by (country, id) for a reproducible checksum",
        },
    }
    if report is not None:
        manifest["ingest_report"] = {
            "entries_seen": report.entries_seen,
            "reviews_kept": report.reviews_kept,
            "total_dropped": report.total_dropped,
            "drops": dict(report.drops),
            "field_gaps": dict(report.field_gaps),
            "cross_country_duplicate_ids": [
                {"id": rid, "first_seen": first, "seen_again": second}
                for rid, first, second in report.cross_country_ids
            ],
            "reconciles": report.reconciles(),
        }
    return manifest


def write_snapshot(
    reviews: list[dict],
    config: Config,
    report: IngestReport | None = None,
    snapshot_dir: Path | None = None,
    when: datetime | None = None,
    force: bool = False,
) -> tuple[Path, Path, bool]:
    """Write the corpus and its manifest.

    Returns (snapshot_path, manifest_path, was_written).

    Refuses to overwrite a same-day snapshot whose contents differ, because a
    snapshot silently changing under an eval is the exact failure this design
    exists to prevent. An identical rewrite is a no-op rather than an error.
    """
    snapshot_dir = Path(snapshot_dir) if snapshot_dir else config.snapshot_dir
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    stem = snapshot_stem(config, when)
    snapshot_path = snapshot_dir / f"{stem}{SNAPSHOT_SUFFIX}"
    manifest_path = snapshot_dir / f"{stem}{MANIFEST_SUFFIX}"

    payload = serialize(reviews)
    checksum = sha256_of(payload)

    if snapshot_path.exists() and not force:
        existing = snapshot_path.read_text(encoding="utf-8")
        if sha256_of(existing) == checksum:
            return snapshot_path, manifest_path, False
        raise SnapshotExists(
            f"{snapshot_path.name} already exists and its contents differ "
            f"({len(existing.splitlines())} reviews on disk, {len(reviews)} now).\n"
            "Snapshots are frozen on purpose - an eval scored against the old one "
            "would silently change meaning.\n"
            "Pass --force to overwrite deliberately, or wait until tomorrow for a "
            "new dated snapshot."
        )

    snapshot_path.write_text(payload, encoding="utf-8")
    manifest = build_manifest(
        reviews, config, report, snapshot_path.name, checksum, len(payload.encode("utf-8"))
    )
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return snapshot_path, manifest_path, True


def load_snapshot(path: Path) -> list[dict]:
    path = Path(path)
    reviews = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            reviews.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{number} is not valid JSON: {exc}") from exc
    return reviews


def manifest_for(snapshot_path: Path) -> Path:
    name = Path(snapshot_path).name
    if name.endswith(SNAPSHOT_SUFFIX):
        name = name[: -len(SNAPSHOT_SUFFIX)]
    return Path(snapshot_path).parent / f"{name}{MANIFEST_SUFFIX}"


def latest_snapshot(snapshot_dir: Path) -> Path | None:
    """Newest snapshot by filename. Dates sort correctly because they are ISO."""
    candidates = sorted(Path(snapshot_dir).glob(f"*{SNAPSHOT_SUFFIX}"))
    return candidates[-1] if candidates else None


def verify_snapshot(snapshot_path: Path) -> tuple[bool, str]:
    """Check a snapshot against its manifest checksum.

    The question phase 5 needs answerable: is this still the corpus I scored
    against?
    """
    snapshot_path = Path(snapshot_path)
    manifest_path = manifest_for(snapshot_path)
    if not snapshot_path.exists():
        return False, f"missing snapshot: {snapshot_path}"
    if not manifest_path.exists():
        return False, f"missing manifest: {manifest_path}"

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    actual = sha256_of(snapshot_path.read_text(encoding="utf-8"))
    expected = manifest.get("sha256")
    if actual != expected:
        return False, (
            f"checksum mismatch for {snapshot_path.name}\n"
            f"  manifest: {expected}\n"
            f"  on disk:  {actual}\n"
            "The corpus has changed since it was frozen."
        )

    actual_rows = len(load_snapshot(snapshot_path))
    expected_rows = manifest.get("review_count")
    if actual_rows != expected_rows:
        return False, (
            f"row count mismatch: manifest says {expected_rows}, file has {actual_rows}"
        )
    return True, (
        f"{snapshot_path.name}: OK - {actual_rows} reviews, sha256 {actual[:16]}..."
    )
