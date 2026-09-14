# Voice-of-Customer Analysis — Ingest Layer

Pulls real App Store reviews onto disk and freezes them as a stable corpus.

Phase 1 only. No clustering, no LLM calls, no UI, no evals — see `CLAUDE.md`.

## Setup on a Mac, from nothing

**1. Check your Python version.** You need 3.11 or newer.

```bash
python3 --version
```

If that prints 3.11 or higher, skip to step 3.

**2. Install Python 3.11+ if you need it.**

```bash
# Install Homebrew first if you don't have it:
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

brew install python@3.12
python3 --version   # confirm it now reports 3.12.x
```

**3. Clone the repo.**

```bash
git clone https://github.com/sukratishm/Voice-of-Customer-analysis.git
cd Voice-of-Customer-analysis
git checkout claude/voc-ingest-layer-jj2pfv
```

**There is nothing to `pip install`.** The ingest layer uses only the Python
standard library. No virtualenv, no requirements.txt, no dependencies to break.

**4. Confirm it works before touching the network.**

```bash
python3 -m unittest discover -s tests -t .
```

Expect `OK` and ~89 tests. These run entirely on fixtures.

## Pulling the reviews

```bash
python3 -m ingest snapshot
```

That does everything: fetches every storefront in `config.toml`, caches each
HTTP response, normalizes and dedupes, writes a dated snapshot plus a manifest,
and prints the ingest report and a summary.

First run takes a couple of minutes — there is a ~1s politeness delay between
requests and roughly 50 pages to walk. **Every run after that is instant**,
because it reads the cache and makes zero network calls.

Then commit the corpus:

```bash
git add data/snapshots/
git commit -m "Add Duolingo App Store snapshot"
git push
```

`data/raw/` is gitignored — it is a disposable cache, safe to delete at any time.

## Everyday commands

```bash
# Re-print the summary. Different five reviews each time.
python3 -m ingest summarize

# The same five as a previous run — the seed is printed in every summary.
python3 -m ingest summarize --seed 42

# Show more examples.
python3 -m ingest summarize --samples 20

# Is this still the corpus my evals were scored against?
python3 -m ingest verify

# Warm the cache without building a snapshot.
python3 -m ingest fetch

# One storefront only, useful for a quick check.
python3 -m ingest fetch --country us

# See what is happening, request by request.
python3 -m ingest snapshot --verbose
```

## Changing the app

Edit `config.toml`:

```toml
[app]
name = "Notion"
slug = "notion"
apple_app_id = "1232780281"   # the number after "id" in the App Store URL
```

The cache is keyed by app id, so a different app gets its own cache and its own
snapshot. Nothing collides, and the old corpus stays valid.

## Things that will happen, and what they mean

**"REFUSED: ... already exists and its contents differ"** — you already built a
snapshot today and today's pull found different reviews. This is the design
working: a snapshot must not change under an eval that was scored against it.
Either keep the existing one, or pass `--force` if you deliberately want to
replace it.

**Pages failing during a pull** — normal. Apple's feed is undocumented and
flaky. A failed page is reported and skipped; a storefront gives up after three
failures in a row. Failures are never cached, so simply re-running retries them.

**Fewer reviews than you expected** — read the ingest report. Every dropped
entry is counted under a named reason with examples. The numbers reconcile:
entries in = reviews out + dropped.

## What the output tells you

- **Ingest report** — where every entry went, by named reason
- **Coverage gaps** — kept reviews missing an optional field
- **`app_version` coverage** — the real denominator for release-over-release
  analysis, since `created_at` cannot support trend charts (see ADR 0002)
- **Snapshot + manifest** — the frozen corpus and its sha256

## Layout

```
config.toml        Which app, which storefronts, politeness settings
ingest/            Fetch, normalize, snapshot, summarize, CLI
data/raw/          HTTP cache — gitignored, disposable
data/snapshots/    Frozen corpora — committed, immutable
docs/decisions/    ADRs
tests/             89 tests, no network required
```
