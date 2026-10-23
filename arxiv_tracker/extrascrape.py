# -*- coding: utf-8 -*-
import re
import requests

UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"
)

# 支持的“代码托管域名”
_CODE_HOSTS = r"(?:github\.com|gitlab\.com|huggingface\.co|gitee\.com)"
_URL_TAIL = r'[^\s\]\)\<\>"\'\u3002\uFF0C\uFF1B\u3001]+'  # 去掉常见结尾标点
_RE_CODE_URL = re.compile(rf"https?://{_CODE_HOSTS}/{_URL_TAIL}", re.I)

def _norm_url(u: str) -> str:
    # 去掉结尾多余的标点/括号
    return u.rstrip('.,;:)]>}’”"\'，。；：）】》')

def _extract_from_text(s: str):
    if not s:
        return []
    return [_norm_url(m.group(0)) for m in _RE_CODE_URL.finditer(s)]

def _get(url: str, timeout: int = 10):
    return requests.get(
        url,
        headers={"User-Agent": UA, "Accept": "*/*"},
        timeout=timeout,
        allow_redirects=True,
    )

def _extract_from_html(url: str, timeout: int):
    try:
        r = _get(url, timeout=timeout)
        r.raise_for_status()
        return _extract_from_text(r.text)
    except Exception:
        return []

def _extract_from_pdf_head(pdf_url: str, timeout: int, head_bytes: int = 256 * 1024):
    """
    只取 PDF 前 head_bytes（默认 256KB），用 bytes 正则匹配 URL。
    """
    try:
        headers = {
            "User-Agent": UA,
            "Range": f"bytes=0-{head_bytes-1}",
            "Accept": "application/pdf,*/*",
        }
        with requests.get(pdf_url, headers=headers, timeout=timeout,
                          allow_redirects=True, stream=True) as response:
            # Range 可能被忽略；流式读取并主动截断，避免下载整篇 PDF。
            if response.status_code not in (200, 206):
                return []
            chunks, remaining = [], head_bytes
            for chunk in response.iter_content(chunk_size=min(head_bytes, 64 * 1024)):
                piece = chunk[:remaining]
                chunks.append(piece)
                remaining -= len(piece)
                if remaining <= 0:
                    break
            data = b"".join(chunks)
        # Unicode 正则含有 \u 转义，不能直接编译为 bytes 正则；latin-1 可无损映射字节。
        return _extract_from_text(data.decode("latin-1"))
    except Exception:
        return []

def _dedup(urls):
    seen, out = set(), []
    for u in urls:
        k = u.strip()
        if not k:
            continue
        lk = k.lower()
        if lk not in seen:
            seen.add(lk)
            out.append(k)
    return out

def augment_item_links(
    item: dict,
    *,
    html: bool = True,
    pdf_if_missing: bool = True,   # << 关键开关：仅在“没有 code 链接时”才去扫 PDF
    pdf_first_page: bool = False,  # 始终扫 PDF（不建议默认开，性能差）
    timeout: int = 10,
) -> int:
    """
    返回本次新增的链接条数
    """
    code_urls = list(item.get("code_urls") or [])
    before = len(code_urls)

    # 1) 从已有文本（summary / comments / title）里先挖
    code_urls += _extract_from_text(item.get("summary") or "")
    code_urls += _extract_from_text(item.get("comments") or "")
    code_urls += _extract_from_text(item.get("title") or "")

    # 2) 再尝试 HTML 页
    if html and item.get("html_url"):
        code_urls += _extract_from_html(item["html_url"], timeout=timeout)

    code_urls = _dedup(code_urls)

    # 3) 仅当目前还没有 code 链接时，再去扫 PDF 头
    need_pdf = (pdf_if_missing and len(code_urls) == 0) or pdf_first_page
    if need_pdf and item.get("pdf_url"):
        code_urls += _extract_from_pdf_head(item["pdf_url"], timeout=timeout)
        code_urls = _dedup(code_urls)

    item["code_urls"] = code_urls
    return len(code_urls) - before
