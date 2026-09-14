"""Load and validate config.toml.

Config lives in a file rather than in code because the app under analysis is
expected to change. Nothing here knows about Duolingo.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

if sys.version_info < (3, 11):  # pragma: no cover - environment guard
    raise SystemExit(
        "This project needs Python 3.11 or newer (it reads config.toml with the\n"
        "standard-library tomllib module, added in 3.11).\n"
        f"You are running {sys.version.split()[0]} from {sys.executable}."
    )

import tomllib

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = REPO_ROOT / "config.toml"


class ConfigError(Exception):
    """Raised when config.toml is missing, malformed, or has a bad value."""


@dataclass(frozen=True)
class AppConfig:
    name: str
    slug: str
    apple_app_id: str


@dataclass(frozen=True)
class AppleSourceConfig:
    countries: tuple[str, ...]
    max_pages: int
    request_delay_seconds: float
    request_timeout_seconds: float
    max_retries: int
    user_agent: str


@dataclass(frozen=True)
class Config:
    app: AppConfig
    apple: AppleSourceConfig
    repo_root: Path

    @property
    def raw_dir(self) -> Path:
        """Cached HTTP responses. Gitignored and disposable."""
        return self.repo_root / "data" / "raw"

    @property
    def snapshot_dir(self) -> Path:
        """Frozen corpora. Committed, treated as immutable."""
        return self.repo_root / "data" / "snapshots"


def _require(mapping: dict, key: str, where: str) -> object:
    if key not in mapping:
        raise ConfigError(f"config.toml is missing [{where}] {key!r}")
    return mapping[key]


def _as_str(value: object, where: str, key: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"[{where}] {key!r} must be a non-empty string, got {value!r}")
    return value.strip()


def _as_positive_number(value: object, where: str, key: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
        raise ConfigError(f"[{where}] {key!r} must be a positive number, got {value!r}")
    return float(value)


def load_config(path: Path | None = None) -> Config:
    """Read config.toml and return a validated Config.

    Fails loudly with a readable message rather than half-loading, because a
    typo'd app id would otherwise surface as an empty corpus much later.
    """
    path = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    if not path.exists():
        raise ConfigError(f"No config file at {path}")

    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path} is not valid TOML: {exc}") from exc

    app_raw = _require(data, "app", "app")
    if not isinstance(app_raw, dict):
        raise ConfigError("[app] must be a table")

    app = AppConfig(
        name=_as_str(_require(app_raw, "name", "app"), "app", "name"),
        slug=_as_str(_require(app_raw, "slug", "app"), "app", "slug"),
        apple_app_id=_as_str(
            _require(app_raw, "apple_app_id", "app"), "app", "apple_app_id"
        ),
    )
    if not app.apple_app_id.isdigit():
        raise ConfigError(
            f"[app] apple_app_id must be numeric, got {app.apple_app_id!r}. "
            "It is the number after 'id' in the App Store URL."
        )
    if not app.slug.replace("-", "").replace("_", "").isalnum():
        raise ConfigError(
            f"[app] slug must be alphanumeric with - or _ only, got {app.slug!r}. "
            "It ends up in filenames."
        )

    apple_raw = data.get("source", {}).get("apple")
    if not isinstance(apple_raw, dict):
        raise ConfigError("config.toml is missing the [source.apple] table")

    countries_raw = _require(apple_raw, "countries", "source.apple")
    if not isinstance(countries_raw, list) or not countries_raw:
        raise ConfigError("[source.apple] countries must be a non-empty list")
    countries = []
    for item in countries_raw:
        code = _as_str(item, "source.apple", "countries").lower()
        if not (code.isalpha() and len(code) == 2):
            raise ConfigError(
                f"[source.apple] countries entries must be 2-letter codes, got {item!r}"
            )
        if code not in countries:
            countries.append(code)

    max_pages = _require(apple_raw, "max_pages", "source.apple")
    if not isinstance(max_pages, int) or isinstance(max_pages, bool) or max_pages < 1:
        raise ConfigError(f"[source.apple] max_pages must be a positive int, got {max_pages!r}")

    max_retries = _require(apple_raw, "max_retries", "source.apple")
    if not isinstance(max_retries, int) or isinstance(max_retries, bool) or max_retries < 1:
        raise ConfigError(
            f"[source.apple] max_retries must be a positive int, got {max_retries!r}"
        )

    apple = AppleSourceConfig(
        countries=tuple(countries),
        max_pages=max_pages,
        request_delay_seconds=_as_positive_number(
            _require(apple_raw, "request_delay_seconds", "source.apple"),
            "source.apple",
            "request_delay_seconds",
        ),
        request_timeout_seconds=_as_positive_number(
            _require(apple_raw, "request_timeout_seconds", "source.apple"),
            "source.apple",
            "request_timeout_seconds",
        ),
        max_retries=max_retries,
        user_agent=_as_str(
            _require(apple_raw, "user_agent", "source.apple"), "source.apple", "user_agent"
        ),
    )

    return Config(app=app, apple=apple, repo_root=REPO_ROOT)
