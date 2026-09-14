# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import time
import random
import requests
from typing import Dict, Optional

# 首选 HTTPS，失败时回退到 HTTP（某些网络下 HTTPS 易读超）
ARXIV_HTTPS = "https://export.arxiv.org/api/query"
ARXIV_HTTP  = "http://export.arxiv.org/api/query"

# 可通过环境变量调整（Windows PowerShell 示例见下）
DEFAULT_TIMEOUT = float(os.getenv("ARXIV_TIMEOUT", "45"))      # 单次请求超时（秒）
MAX_ATTEMPTS    = int(os.getenv("ARXIV_MAX_ATTEMPTS", "6"))    # 尝试次数
BASE_PAUSE      = float(os.getenv("ARXIV_PAUSE", "1.5"))       # 基础退避（秒）
MAX_SLEEP       = float(os.getenv("ARXIV_MAX_SLEEP", "20"))    # 退避上限（秒）

RETRYABLE_STATUS = {429, 500, 502, 503, 504}

HEADERS = {
    "User-Agent": os.getenv(
        "ARXIV_UA",
        "Arxiv-tracker/1.0 (+https://github.com/uuiii221/Arxiv-tracker)"
    ),
    "Accept": "application/atom+xml,application/xml;q=0.9,*/*;q=0.8",
}

_session = requests.Session()


def _sleep_backoff(attempt: int, retry_after: Optional[float] = None) -> None:
    if retry_after is not None:
        delay = min(max(retry_after, 3.0), MAX_SLEEP)
    else:
        delay = min(
            BASE_PAUSE * (2 ** (attempt - 1)) + random.uniform(0, 1.0),
            MAX_SLEEP
        )

    print(f"[arXiv] waiting {delay:.1f}s before retry...", flush=True)
    time.sleep(delay)


def _do_get(
    base_url: str,
    params: Dict[str, str],
    timeout: Optional[float] = None
) -> requests.Response:

    timeout = timeout or DEFAULT_TIMEOUT
    last_err: Optional[Exception] = None

    for attempt in range(1, MAX_ATTEMPTS + 1):
        retry_after = None

        try:
            resp = _session.get(
                base_url,
                params=params,
                headers=HEADERS,
                timeout=timeout
            )

            if resp.status_code in RETRYABLE_STATUS:
                raise requests.exceptions.HTTPError(
                    f"HTTP {resp.status_code}",
                    response=resp
                )

            return resp

        except (
            requests.exceptions.Timeout,
            requests.exceptions.ReadTimeout,
            requests.exceptions.ConnectionError
        ) as e:

            last_err = e

            print(
                f"[arXiv] request failed "
                f"attempt={attempt}/{MAX_ATTEMPTS} "
                f"url={base_url} "
                f"error={type(e).__name__}: {e}",
                flush=True,
            )

        except requests.exceptions.HTTPError as e:

            last_err = e
            st = getattr(e.response, "status_code", None)

            print(
                f"[arXiv] HTTP error "
                f"attempt={attempt}/{MAX_ATTEMPTS} "
                f"url={base_url} "
                f"status={st}",
                flush=True,
            )

            if st not in RETRYABLE_STATUS:
                break

            if st == 429 and e.response is not None:
                value = e.response.headers.get("Retry-After")

                if value:
                    try:
                        retry_after = float(value)
                    except ValueError:
                        retry_after = None

        if attempt < MAX_ATTEMPTS:
            _sleep_backoff(
                attempt,
                retry_after=retry_after
            )

    if last_err:
        raise last_err

    raise RuntimeError("Unknown arXiv request error.")


def fetch_arxiv_feed(query: str,
                     start: int = 0,
                     max_results: int = 10,
                     sort_by: str = "submittedDate",
                     sort_order: str = "descending") -> str:
    """
    拉取 arXiv Atom Feed。先 HTTPS，失败则 HTTP 回退。
    """
    params = {
        "search_query": query,
        "start": str(start),
        "max_results": str(max_results),
        "sortBy": sort_by,
        "sortOrder": sort_order,
    }

    last_err: Optional[Exception] = None
    for base in (ARXIV_HTTPS, ARXIV_HTTP):
        try:
            r = _do_get(base, params, timeout=DEFAULT_TIMEOUT)
            r.raise_for_status()
            return r.text
        except Exception as e:
            last_err = e
            # 换下一个 base 继续
            continue

    # 两个 base 都失败
    assert last_err is not None
    raise last_err
