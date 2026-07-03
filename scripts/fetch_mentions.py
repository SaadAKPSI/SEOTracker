#!/usr/bin/env python3
"""
fetch_mentions.py
-----------------
Fetch mentions of "Alpha Kappa Psi" from free RSS feeds (Google Alerts +
optional public news feeds), filter for relevance, run a lightweight
sentiment heuristic, deduplicate, and append new items to data/mentions.json.

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
# Configuration
# --------------------------------------------------------------------------- #

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

# Each query is URL-encoded. %22 = double-quote (forces exact phrase).
_QUERIES = [
    '%22Alpha+Kappa+Psi%22',
    '%22Alpha+Kappa+Psi%22+fraternity',
    '%22Alpha+Kappa+Psi%22+chapter',
    '%22Alpha+Kappa+Psi%22+brotherhood',
    '%22Alpha+Kappa+Psi%22+philanthropy',
    '%22Alpha+Kappa+Psi%22+business+fraternity',
    '%22Alpha+Kappa+Psi%22+when:30d',   # recency-biased pass for fresh items
]

FEEDS = [_GNEWS.format(q=q) for q in _QUERIES]

# Paste your own Google Alerts RSS feed URL(s) here for cleaner direct links.
# See README for how to create one (free, no key). Example:
#   FEEDS.append("https://www.google.com/alerts/feeds/00000.../00000...")

# Allow the environment to inject feeds (comma-separated) without code edits.
if os.environ.get("AKPSI_FEEDS"):
    FEEDS = [u.strip() for u in os.environ["AKPSI_FEEDS"].split(",") if u.strip()]

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
# Sentiment (keyword heuristic — no external NLP dependency)
# --------------------------------------------------------------------------- #

POSITIVE_WORDS = {
    "award", "awards", "honor", "honored", "celebrate", "celebrated",
    "success", "successful", "achievement", "achievements", "win", "wins",
    "winner", "recognized", "recognition", "growth", "raised", "charity",
    "volunteer", "volunteered", "scholarship", "excellence", "proud",
    "leadership", "philanthropy", "donate", "donated", "milestone",
    "welcome", "inducted", "thriving", "praise", "outstanding",
}

NEGATIVE_WORDS = {
    "hazing", "lawsuit", "suspended", "suspension", "expelled", "banned",
    "investigation", "misconduct", "scandal", "death", "died", "arrest",
    "arrested", "charged", "violation", "probation", "controversy",
    "allegation", "allegations", "accused", "fine", "fined", "revoked",
    "disciplinary", "assault", "complaint", "dropped",
}


def classify_sentiment(text: str) -> str:
    """Return 'positive', 'negative', or 'neutral' from a keyword tally."""
    tokens = re.findall(r"[a-z']+", (text or "").lower())
    pos = sum(1 for t in tokens if t in POSITIVE_WORDS)
    neg = sum(1 for t in tokens if t in NEGATIVE_WORDS)
    if neg > pos:
        return "negative"
    if pos > neg:
        return "positive"
    return "neutral"


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


def normalize_entry(entry, feed) -> dict | None:
    title = strip_html(getattr(entry, "title", ""))
    summary = strip_html(getattr(entry, "summary", getattr(entry, "description", "")))
    if not is_relevant(title, summary):
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
        "sentiment": classify_sentiment(f"{title} {summary}"),
    }


def run() -> int:
    os.makedirs(DATA_DIR, exist_ok=True)
    existing = load_existing()
    seen_urls = {item.get("url") for item in existing}

    new_items = []
    for url in FEEDS:
        feed = fetch_feed(url)
        if not feed:
            continue
        for entry in getattr(feed, "entries", []):
            item = normalize_entry(entry, feed)
            if item and item["url"] not in seen_urls:
                seen_urls.add(item["url"])
                new_items.append(item)

    if not new_items:
        print("No new mentions found.")
        # Still ensure the file exists for the dashboard.
        if not os.path.exists(DATA_FILE):
            with open(DATA_FILE, "w", encoding="utf-8") as fh:
                json.dump(existing, fh, indent=2, ensure_ascii=False)
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
