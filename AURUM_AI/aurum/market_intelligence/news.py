"""News / announcement adapters.

* ``OfficialFeedProvider`` - central banks' own RSS feeds (published for automated reading; no key). Items are
  classified ``official_announcement`` because the publisher is the institution itself.
* ``FinnhubNewsProvider`` - Finnhub market news (``GET /api/v1/news?category=general``), only with an API key.
  Items are ``reported_news``: third-party reporting, not confirmed announcements.

Headlines are stored exactly as published with their URL and publication time; AURUM generates no headlines and no
summaries here. A headline is context, not an actionable catalyst.
"""
from __future__ import annotations

import hashlib
import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

from aurum.market_intelligence.http import HttpClient, ProviderError

log = logging.getLogger(__name__)
UTC = timezone.utc
SOURCE_TYPES = ("official_announcement", "reported_news", "commentary", "unverified")
BANK_CURRENCY = {"fed": "USD", "ecb": "EUR", "boe": "GBP", "boj": "JPY", "rba": "AUD", "rbnz": "NZD", "boc": "CAD",
                 "snb": "CHF"}


def _pubdate(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        d = parsedate_to_datetime(s.strip())
        return (d if d.tzinfo else d.replace(tzinfo=UTC)).astimezone(UTC)
    except (TypeError, ValueError):
        try:
            d = datetime.fromisoformat(s.strip().replace("Z", "+00:00"))
            return (d if d.tzinfo else d.replace(tzinfo=UTC)).astimezone(UTC)
        except ValueError:
            return None


def parse_rss(xml_text: str) -> list[dict[str, Any]]:
    root = ET.fromstring(xml_text.lstrip("﻿").strip())
    items = root.findall(".//item")
    out = []
    for it in items:
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        if not title or not link:
            continue
        out.append({"headline": title, "url": link, "guid": (it.findtext("guid") or link).strip(),
                    "published_at": _pubdate(it.findtext("pubDate") or it.findtext("{http://purl.org/dc/elements/1.1/}date")),
                    "description": (it.findtext("description") or "").strip()[:500] or None})
    return out


class OfficialFeedProvider:
    name = "official_feeds"

    def __init__(self, feeds: list[dict[str, Any]], http: HttpClient | None = None):
        self.feeds = feeds
        self.http = http or HttpClient(timeout=30, max_retries=2)

    def fetch(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        items, errors = [], []
        now = datetime.now(UTC)
        for f in self.feeds:
            try:
                parsed = parse_rss(self.http.get(f["url"]).text)
            except (ProviderError, ET.ParseError) as exc:
                errors.append({"feed": f["id"], "error": str(exc)})
                continue
            cur = BANK_CURRENCY.get(f.get("bank", ""))
            for p in parsed:
                items.append({**p, "source": f["id"], "provider": self.name, "category": f.get("category"),
                              "source_type": "official_announcement", "currencies": [cur] if cur else [],
                              "reliability": "primary source (issuing institution)", "retrieved_at": now,
                              "item_uid": hashlib.sha1(f"{f['id']}|{p['guid']}".encode()).hexdigest()[:32]})
        return items, errors


class FinnhubNewsProvider:
    name = "finnhub_news"

    def __init__(self, api_key: str, http: HttpClient | None = None):
        self.key = api_key
        self.http = http or HttpClient(secrets=[api_key])

    def fetch(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        try:
            data = self.http.json("https://finnhub.io/api/v1/news", {"category": "general", "token": self.key})
        except ProviderError as exc:
            return [], [{"feed": self.name, "error": str(exc)}]
        now, out = datetime.now(UTC), []
        for r in data if isinstance(data, list) else []:
            if not r.get("headline") or not r.get("url"):
                continue
            out.append({"headline": r["headline"], "url": r["url"], "guid": str(r.get("id") or r["url"]),
                        "published_at": datetime.fromtimestamp(int(r["datetime"]), UTC) if r.get("datetime") else None,
                        "description": None, "source": str(r.get("source") or "finnhub"), "provider": self.name,
                        "category": r.get("category") or "general", "source_type": "reported_news",
                        "currencies": [], "reliability": "third-party news aggregator", "retrieved_at": now,
                        "item_uid": hashlib.sha1(f"finnhub|{r.get('id') or r['url']}".encode()).hexdigest()[:32]})
        return out, []
