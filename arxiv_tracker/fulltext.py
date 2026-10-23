"""Download an entire arXiv PDF and extract every page before summarization."""
from hashlib import sha256
from io import BytesIO
import re

from pypdf import PdfReader
import requests

from .client import HEADERS, _wait_for_arxiv


class FullTextError(RuntimeError):
    pass


def _pdf_url(item):
    # Use the versioned arXiv ID returned by the feed, never a page's arbitrary links.
    identifier = re.sub(r"^https?://(?:export\.)?arxiv\.org/abs/", "", item.get("id") or "")
    if not re.fullmatch(r"(?:\d{4}\.\d{4,5}|[a-zA-Z.-]+/\d{7})(?:v\d+)?", identifier):
        raise FullTextError("Missing or invalid arXiv ID for full-text retrieval")
    return f"https://arxiv.org/pdf/{identifier}"


def extract_pdf_text(data, *, max_pages=150, max_chars=200000):
    if not data.startswith(b"%PDF-"):
        raise FullTextError("Full-text response is not a PDF")
    try:
        reader = PdfReader(BytesIO(data))
        if reader.is_encrypted and not reader.decrypt(""):
            raise FullTextError("Cannot read encrypted PDF")
        page_count = len(reader.pages)
        if not 1 <= page_count <= max_pages:
            raise FullTextError(f"PDF has {page_count} pages; limit is {max_pages}; no pages were truncated")
        pages, text_pages = [], 0
        for index, page in enumerate(reader.pages, 1):
            text = (page.extract_text(extraction_mode="layout") or "").strip()
            text_pages += bool(text)
            pages.append(f"[Page {index}]\n{text or '[No extractable text on this page]'}")
        text = "\n\n".join(pages)
        if sum(len(value) for value in pages) < 1500 or text_pages < max(1, page_count * 0.75):
            raise FullTextError("PDF has too little extractable text; cannot produce a full-paper report")
        if len(text) > max_chars:
            raise FullTextError(f"Full text exceeds {max_chars} characters; increase the limit instead of truncating")
        return text, page_count, text_pages
    except FullTextError:
        raise
    except Exception as exc:
        raise FullTextError(f"PDF parsing failed: {exc}") from exc


def load_paper_fulltext(item, config=None):
    config = config or {}
    url = _pdf_url(item)
    limit = int(config.get("max_bytes", 30 * 1024 * 1024))
    timeout = int(config.get("timeout", 60))
    try:
        _wait_for_arxiv()
        with requests.get(url, headers=HEADERS, timeout=(10, timeout), stream=True) as response:
            response.raise_for_status()
            size = response.headers.get("Content-Length")
            if size and int(size) > limit:
                raise FullTextError(f"Full PDF exceeds download limit ({limit} bytes)")
            chunks, received = [], 0
            for chunk in response.iter_content(chunk_size=64 * 1024):
                received += len(chunk)
                if received > limit:
                    raise FullTextError(f"Full PDF exceeds download limit ({limit} bytes)")
                chunks.append(chunk)
            data = b"".join(chunks)
        text, pages, text_pages = extract_pdf_text(
            data, max_pages=int(config.get("max_pages", 150)),
            max_chars=int(config.get("max_chars", 200000)),
        )
        return text, {"url": url, "format": "pdf", "pages": pages, "text_pages": text_pages,
                      "characters": len(text), "sha256": sha256(data).hexdigest()}
    except FullTextError:
        raise
    except Exception as exc:
        raise FullTextError(f"Full-text retrieval failed for {item.get('id')}: {exc}") from exc
