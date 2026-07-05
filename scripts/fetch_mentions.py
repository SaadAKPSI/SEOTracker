#!/usr/bin/env python3
"""
fetch_mentions.py
-----------------
Fetch mentions of "Alpha Kappa Psi" from free RSS feeds (Google Alerts +
optional public news feeds), filter for relevance, grade each mention by how
central Alpha Kappa Psi is to the article (AKPsi relevance: high/medium/low),
deduplicate, and append new items to data/mentions.json.

Zero paid APIs. Requires only: requests, feedparser.
"""

from __future__ import annotations

import html
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse, parse_qs, unquote

import feedparser
import requests

# --------------------------------------------------------------------------- #
# Feed sources (all free, no API keys)
# --------------------------------------------------------------------------- #
#
# We query Google News RSS with several angles so coverage stays broad but
# relevant. Google News returns the freshest matching articles per query, and
# because the workflow runs every 30 minutes, newly published pieces get picked
# up automatically. The relevance filter below drops anything off-topic, and
# results are deduplicated by URL so overlapping queries don't create repeats.

_GNEWS = "https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"

# Each query is URL-encoded. %22 = double-quote, which forces Google to return
# only articles that contain the EXACT phrase "Alpha Kappa Psi" somewhere in the
# article (headline OR body). Because Google guarantees the phrase, these feeds
# are "trusted": every returned item is a real body-level mention, so we accept
# them without also requiring the phrase in the RSS title.
_PHRASE_QUERIES = [
    '%22Alpha+Kappa+Psi%22',
    '%22Alpha+Kappa+Psi%22+when:1y',      # last 12 months
    '%22Alpha+Kappa+Psi%22+when:6m',      # last 6 months
    '%22Alpha+Kappa+Psi%22+when:30d',     # last 30 days (fresh)
    '%22Alpha+Kappa+Psi%22+fraternity',
    '%22Alpha+Kappa+Psi%22+chapter',
    '%22Alpha+Kappa+Psi%22+philanthropy',
]

# Feeds are (url, trusted). trusted=True -> accept every returned item, because
# Google already verified the exact phrase.
FEEDS = [(_GNEWS.format(q=q), True) for q in _PHRASE_QUERIES]

# Allow the environment to inject trusted feeds (comma-separated) without edits.
if os.environ.get("AKPSI_FEEDS"):
    FEEDS = [(u.strip(), True) for u in os.environ["AKPSI_FEEDS"].split(",") if u.strip()]

# Paths (resolved relative to repo root, one level above this script).
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(REPO_ROOT, "data")
DATA_FILE = os.path.join(DATA_DIR, "mentions.json")

# Keep the dataset from bloating indefinitely.
MAX_ENTRIES = 1000

# Networking
USER_AGENT = "akpsi-dashboard/1.0 (+https://github.com/)"
REQUEST_TIMEOUT = 30

# --------------------------------------------------------------------------- #
# Relevance filtering
# --------------------------------------------------------------------------- #

# Exact phrase (case-insensitive), tolerant of extra whitespace.
PHRASE_RE = re.compile(r"alpha\s+kappa\s+psi", re.IGNORECASE)

# "AKPsi" is only accepted when clearly about the fraternity/business context.
AKPSI_RE = re.compile(r"\bak[\s\-]?psi\b", re.IGNORECASE)
FRAT_CONTEXT_RE = re.compile(
    r"\b(fraternit|brotherhood|business fraternit|professional fraternit|"
    r"chapter|pledge|rush|greek|colony|collegiate|initiat)\w*",
    re.IGNORECASE,
)

# --------------------------------------------------------------------------- #
# AKPsi relevance grading (keyword heuristic - no external NLP dependency)
# --------------------------------------------------------------------------- #
#
# Relevance measures how CENTRAL Alpha Kappa Psi is to an article - not its
# tone. This deliberately replaces the older positive/neutral/negative
# sentiment tag, which mislabeled respectful obituaries and accident coverage
# (where a member's AKPsi affiliation is merely noted) as "negative" and
# overstated the amount of bad press for a nationals-facing dashboard.
#
#   high   - Alpha Kappa Psi is the subject of the headline: chapter features,
#            member/alumni spotlights centered on AKPsi, charters/installations,
#            awards or recognition of AKPsi, conventions, philanthropy drives.
#   medium - AKPsi is a notable-but-shared element: business-fraternity / Greek-
#            life stories, rush coverage, alumni/graduate spotlights, or general
#            fraternity news that features AKPsi among others.
#   low    - AKPsi is a passing, incidental mention: obituaries, memorials,
#            accident/death coverage, and off-topic news where the phrase only
#            appears deep in the body.

# Phrase present in the *headline* itself -> the article is about AKPsi.
_TITLE_PHRASE_RE = re.compile(r"alpha\s+kappa\s+psi|\bak[\s\-]?psi\b", re.IGNORECASE)

# Somber / incidental coverage: force low relevance regardless of other signals.
MEMORIAL_WORDS = (
    "obituary", "obituaries", "funeral", "memorial", "in memoriam",
    "passed away", "passes away", "passing of", "celebration of life",
    "tribute", "condolence", "visitation", "crematory", "cremation",
    "dies", "died", "dead", "death", "killed", "fatal", "crash",
    "accident", "plunge", "remembering",
)

# Fraternity / Greek-life context in the headline -> at least medium relevance.
FRAT_TITLE_WORDS = (
    "fraternit", "sororit", "greek", "brotherhood", "chapter", "pledge",
    "rush", "panhellenic", "interfraternity", "colony", "thon", "little 500",
)

# Headline framings that typically feature a member's AKPsi involvement.
FEATURE_TITLE_WORDS = (
    "spotlight", "alumni", "alumnus", "alumna", "graduate", "profile",
    "best & brightest", "best and brightest", "honoree", "scholarship",
    "leadership", "internship",
)


def classify_relevance(title: str, summary: str = "") -> str:
    """Return 'high', 'medium', or 'low' AKPsi relevance from a headline heuristic.

    Summaries from Google News RSS are essentially the headline plus the source
    name, so grading keys primarily off the title.
    """
    t = (title or "").lower()
    blob = f"{title} {summary}".lower()

    # Memorial / accident coverage is incidental to AKPsi -> low, even if the
    # fraternity is named. This is the exact case the sentiment tag mishandled.
    if any(w in blob for w in MEMORIAL_WORDS):
        return "low"

    # Alpha Kappa Psi named in the headline -> the piece is about AKPsi.
    if _TITLE_PHRASE_RE.search(t):
        return "high"

    # Fraternity/Greek context or member-feature framing in the headline -> the
    # article meaningfully involves AKPsi among other subjects.
    if any(w in t for w in FRAT_TITLE_WORDS) or any(w in t for w in FEATURE_TITLE_WORDS):
        return "medium"

    # Phrase only surfaces deep in the body -> incidental mention.
    return "low"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def strip_html(raw: str) -> str:
    """Remove HTML tags/entities and collapse whitespace."""
    if not raw:
        return ""
    text = re.sub(r"<[^>]+>", " ", raw)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def clean_google_url(url: str) -> str:
    """Google Alerts / Google News wrap the real link in a redirect param."""
    if not url:
        return url
    try:
        parsed = urlparse(url)
        if "google." in parsed.netloc:
            qs = parse_qs(parsed.query)
            for key in ("url", "q"):
                if key in qs and qs[key]:
                    return unquote(qs[key][0])
    except Exception:
        pass
    return url


def parse_date(entry) -> str:
    """Return an ISO-8601 UTC date string, best-effort."""
    for attr in ("published", "updated"):
        val = getattr(entry, attr, None)
        if val:
            try:
                dt = parsedate_to_datetime(val)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.astimezone(timezone.utc).isoformat()
            except Exception:
                pass
    for attr in ("published_parsed", "updated_parsed"):
        val = getattr(entry, attr, None)
        if val:
            try:
                return datetime.fromtimestamp(
                    time.mktime(val), tz=timezone.utc
                ).isoformat()
            except Exception:
                pass
    return datetime.now(timezone.utc).isoformat()


def source_from(entry, feed) -> str:
    """Best-effort publication/source name."""
    src = getattr(entry, "source", None)
    if src and getattr(src, "title", None):
        return src.title
    title = getattr(entry, "title", "")
    if " - " in title:  # Google News: "Headline - Publisher"
        return title.rsplit(" - ", 1)[-1].strip()
    feed_title = getattr(feed.feed, "title", "") if hasattr(feed, "feed") else ""
    if feed_title:
        return re.sub(r'^Google Alert\s*-\s*', "", feed_title).strip()
    return "Unknown"


def is_relevant(title: str, summary: str) -> bool:
    """Exact phrase always passes; AKPsi passes only with fraternity context."""
    blob = f"{title} {summary}"
    if PHRASE_RE.search(blob):
        return True
    if AKPSI_RE.search(blob) and FRAT_CONTEXT_RE.search(blob):
        return True
    return False


# --------------------------------------------------------------------------- #
# Core pipeline
# --------------------------------------------------------------------------- #

def fetch_feed(url: str):
    """Download and parse one feed with error handling."""
    try:
        resp = requests.get(
            url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT
        )
        resp.raise_for_status()
        return feedparser.parse(resp.content)
    except requests.RequestException as exc:
        print(f"[warn] request failed for {url}: {exc}", file=sys.stderr)
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] parse failed for {url}: {exc}", file=sys.stderr)
    return None


def load_existing() -> list:
    if not os.path.exists(DATA_FILE):
        return []
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
            return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError) as exc:
        print(f"[warn] could not read existing data: {exc}", file=sys.stderr)
        return []


def migrate_relevance(items: list) -> int:
    """Backfill AKPsi relevance on existing entries and drop the legacy
    sentiment tag. Returns the number of entries changed so callers can decide
    whether a re-save is warranted even when no new mentions were fetched."""
    changed = 0
    for item in items:
        had_sentiment = "sentiment" in item
        needs_relevance = not item.get("relevance")
        if needs_relevance:
            item["relevance"] = classify_relevance(
                item.get("title", ""), item.get("summary", "")
            )
        if had_sentiment:
            item.pop("sentiment", None)
        if needs_relevance or had_sentiment:
            changed += 1
    return changed


def normalize_entry(entry, feed, trusted: bool = False) -> dict | None:
    title = strip_html(getattr(entry, "title", ""))
    summary = strip_html(getattr(entry, "summary", getattr(entry, "description", "")))

    # Trusted feeds (exact-phrase Google News / Google Alerts) already guarantee
    # the phrase appears in the article body, so we skip the title-level check.
    if not trusted and not is_relevant(title, summary):
        return None

    url = clean_google_url(getattr(entry, "link", "").strip())
    if not url:
        return None

    # Google News often appends " - Publisher" to the title; keep it clean.
    clean_title = re.sub(r"\s*-\s*[^-]+$", "", title) if " - " in title else title

    return {
        "title": clean_title or title,
        "source": source_from(entry, feed),
        "date": parse_date(entry),
        "url": url,
        "summary": summary or clean_title,
        "relevance": classify_relevance(clean_title or title, summary),
    }


def run() -> int:
    os.makedirs(DATA_DIR, exist_ok=True)
    existing = load_existing()
    # Re-grade legacy entries (sentiment -> relevance) in place.
    migrated = migrate_relevance(existing)
    if migrated:
        print(f"Re-graded {migrated} existing entr(ies) to AKPsi relevance.")
    seen_urls = {item.get("url") for item in existing}

    new_items = []
    for url, trusted in FEEDS:
        feed = fetch_feed(url)
        if not feed:
            continue
        for entry in getattr(feed, "entries", []):
            item = normalize_entry(entry, feed, trusted=trusted)
            if item and item["url"] not in seen_urls:
                seen_urls.add(item["url"])
                new_items.append(item)

    if not new_items:
        # Persist the migration even when there is nothing new to add.
        if migrated or not os.path.exists(DATA_FILE):
            with open(DATA_FILE, "w", encoding="utf-8") as fh:
                json.dump(existing, fh, indent=2, ensure_ascii=False)
        print("No new mentions found.")
        return 0

    # Newest first: prepend new items, then cap total size.
    combined = new_items + existing
    combined.sort(key=lambda x: x.get("date", ""), reverse=True)
    combined = combined[:MAX_ENTRIES]

    with open(DATA_FILE, "w", encoding="utf-8") as fh:
        json.dump(combined, fh, indent=2, ensure_ascii=False)

    print(f"Added {len(new_items)} new mention(s). Total: {len(combined)}.")
    return len(new_items)


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print(f"[error] fatal: {exc}", file=sys.stderr)
        sys.exit(1)
