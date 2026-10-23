from __future__ import annotations
from typing import List, Dict, Any, Optional
import feedparser
from dateutil import parser as dtp
from .extractors import extract_venue_info, extract_urls

def parse_feed(xml_text: str) -> List[Dict[str, Any]]:
    feed = feedparser.parse(xml_text)
    if feed.get("bozo"):
        raise ValueError("Invalid arXiv Atom feed: {}".format(feed.get("bozo_exception")))
    if feed.get("version") != "atom10":
        raise ValueError("Expected an arXiv Atom feed")
    items: List[Dict[str, Any]] = []
    for e in feed.entries:
        if "/api/errors" in e.get("id", ""):
            raise ValueError("arXiv API error: {}".format(e.get("summary", "unknown error")))
        title = (e.title or "").replace("\n", " ").strip()
        authors = [a.get("name", "") for a in e.get("authors", [])] if "authors" in e else []
        published = e.get("published")
        updated   = e.get("updated")
        published_iso = dtp.parse(published).isoformat() if published else None
        updated_iso   = dtp.parse(updated).isoformat() if updated else None

        html_url = None
        pdf_url  = None
        for link in e.get("links", []):
            if link.get("rel") == "alternate":
                html_url = link.get("href")
            if link.get("title", "").lower() == "pdf" or link.get("type") == "application/pdf":
                pdf_url = link.get("href")

        comments = getattr(e, "arxiv_comment", None) or ""
        journal_ref = getattr(e, "arxiv_journal_ref", None)
        primary_cat = getattr(getattr(e, "arxiv_primary_category", {}), "term", None) or None
        categories = [t.get("term") for t in e.get("tags", []) if t.get("term")]

        venue = extract_venue_info(f"{comments or ''} {journal_ref or ''}")
        url_info = extract_urls(f"{comments or ''}\n{getattr(e, 'summary', '')}")

        item = {
            "id": e.get("id"),
            "title": title,
            "authors": authors,
            "primary_category": primary_cat,
            "categories": categories,
            "published": published_iso,   # 首次提交
            "updated": updated_iso,       # 最新提交
            "comments": comments,
            "journal_ref": journal_ref,
            "venue_inferred": venue,      # 从 comments/journal_ref 推断中的会信息
            "summary": getattr(e, "summary", ""),
            "html_url": html_url,
            "pdf_url": pdf_url,
            "code_urls": url_info.get("code_urls", []),
            "project_urls": url_info.get("project_urls", []),
            "other_urls": [u for u in url_info.get("all_urls", []) if u not in set(url_info.get("code_urls", []) + url_info.get("project_urls", []))],
        }
        items.append(item)
    return items
