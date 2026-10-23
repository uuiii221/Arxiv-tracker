"""Paper identity, persistent deduplication and bounded feed pagination."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re

from .client import fetch_arxiv_feed
from .parser import parse_feed


def paper_key(identifier):
    """Treat HTTP/HTTPS URLs and revised versions as the same paper."""
    value = re.sub(r"^https?://(?:export\.)?arxiv\.org/(?:abs|pdf)/", "", identifier or "")
    value = re.sub(r"\.pdf$", "", value)
    return re.sub(r"v\d+$", "", value)


def load_seen(path):
    if not path or not Path(path).exists():
        return set()
    # Do not silently reset corrupt state: that would resend every paper.
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict):
        identifiers = data.get("ids", list(data))
    elif isinstance(data, list):
        identifiers = data
    else:
        raise ValueError("Invalid dedup state: expected a list or object")
    if not isinstance(identifiers, list) or any(not isinstance(value, str) for value in identifiers):
        raise ValueError("Invalid dedup state: ids must be a list of strings")
    return {paper_key(value) for value in identifiers if value}


def save_seen(path, identifiers):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(
        json.dumps({"ids": sorted(identifiers)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(target)


def _parse_dt(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def collect_papers(query, settings, seen_ids, since_days=0, unique_only=False, max_pages=20):
    cutoff = datetime.now(timezone.utc) - timedelta(days=since_days) if since_days > 0 else None
    want = settings.max_results
    if want <= 0:
        raise ValueError("max_results must be greater than zero")
    page_size = min(200, max(25, want))
    date_field = "updated" if settings.sort_by == "lastUpdatedDate" else "published"
    collected, batch_keys = [], set()

    for page in range(max_pages):
        xml = fetch_arxiv_feed(
            query, start=page * page_size, max_results=page_size,
            sort_by=settings.sort_by, sort_order=settings.sort_order,
        )
        page_items = parse_feed(xml)
        if not page_items:
            return collected
        for item in page_items:
            timestamp = _parse_dt(item.get(date_field))
            if cutoff and timestamp and timestamp < cutoff:
                if settings.sort_order == "descending":
                    return collected
                continue
            key = paper_key(item.get("id"))
            if not key or key in batch_keys or (unique_only and key in seen_ids):
                continue
            collected.append(item)
            batch_keys.add(key)
            if len(collected) >= want:
                return collected
        if len(page_items) < page_size:
            return collected

    raise RuntimeError(
        "Pagination limit reached before exhausting the feed; narrow the query or time window "
        "instead of silently dropping unseen papers."
    )
