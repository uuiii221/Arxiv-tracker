# -*- coding: utf-8 -*-
import html
from typing import Dict, List, Any, Optional
from .rendering import render_report

try:
    from markdown import markdown as _md
except Exception:
    _md = None

def _esc(x: Optional[str]) -> str:
    return html.escape(x or "", quote=True)

def _md2html(md: str) -> str:
    if not md: return ""
    if _md:
        return _md(md, extensions=["extra", "sane_lists", "tables"])
    return "<pre style='white-space:pre-wrap'>" + _esc(md) + "</pre>"

def _strip_redundant_links(md: str) -> str:
    out = []
    for line in (md or "").splitlines():
        if line.strip().lower().startswith("- **links**"):
            continue
        out.append(line)
    return "\n".join(out)

def _join_links(it: Dict[str, Any]) -> str:
    parts = []
    if it.get("html_url"):
        parts.append(f'<a href="{_esc(it["html_url"])}">Abs</a>')
    if it.get("pdf_url"):
        parts.append(f'<a href="{_esc(it["pdf_url"])}">PDF</a>')
    if it.get("code_urls"):
        a = [f'<a href="{_esc(u)}">Code{i+1}</a>' for i,u in enumerate(it["code_urls"][:3])]
        parts.append(" · ".join(a))
    if it.get("project_urls"):
        a = [f'<a href="{_esc(u)}">Project{i+1}</a>' for i,u in enumerate(it["project_urls"][:2])]
        parts.append(" · ".join(a))
    return " · ".join(parts)

CSS = """
.container{font-family:ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,Helvetica,Arial;color:#0f172a}
.card{border:1px solid #e5e7eb;border-radius:14px;padding:14px;margin:12px 0;background:#fff}
.title{font-weight:700;font-size:16px;margin:0 0 6px 0}
.meta{color:#667085;font-size:13px;margin:2px 0}
.links a{color:#2563eb;text-decoration:none;margin-right:10px}
.grid{display:grid;grid-template-columns:1fr;gap:10px}
@media (min-width: 860px){.grid-2{grid-template-columns:1fr 1fr}}
.section{border:1px solid #eef2f7;border-radius:10px;padding:8px 10px;background:#f8fafc}
.section h4{margin:4px 0 6px 0;font-size:14px}
"""

def _render_card(it: Dict[str, Any],
                 t_zh: Optional[Dict[str, str]] = None,
                 sum_zh: Optional[Dict[str, str]] = None,
                 sum_en: Optional[Dict[str, str]] = None) -> str:
    title = it.get("title") or ""
    title_link = it.get("html_url") or "#"
    authors = ", ".join(it.get("authors", []))
    venue = it.get("venue_inferred") or (it.get("journal_ref") or "")
    pub = it.get("published") or "—"
    upd = it.get("updated") or "—"
    comments = it.get("comments") or ""
    summary = it.get("summary") or ""

    zh_title = (t_zh or {}).get("title_zh")
    zh_sum   = (t_zh or {}).get("summary_zh")

    # 新双语总结
    digest_en = (sum_en or {}).get("digest_en") or (sum_zh or {}).get("digest_en") or ""
    digest_zh = (sum_zh or {}).get("digest_zh") or (sum_en or {}).get("digest_zh") or ""

    out = [f'<div class="card">']
    out.append(f'<div class="title"><a href="{_esc(title_link)}">{_esc(title)}</a></div>')
    out.append(f'<div class="meta">Authors: {_esc(authors)}</div>')
    if venue: out.append(f'<div class="meta">Venue: {_esc(venue)}</div>')
    out.append(f'<div class="meta">First: {_esc(pub)} · Latest: {_esc(upd)}</div>')
    if comments: out.append(f'<div class="meta">Comments: {_esc(comments)}</div>')

    links = _join_links(it)
    if links: out.append(f'<div class="links" style="margin:8px 0">{links}</div>')

    if summary:
        out.append('<div class="section"><h4>Abstract</h4><div style="white-space:pre-wrap">'
                   + _esc(summary) + '</div></div>')
    if zh_title or zh_sum:
        zh_parts = []
        if zh_title: zh_parts.append(f"<p><b>标题：</b>{_esc(zh_title)}</p>")
        if zh_sum:   zh_parts.append("<div style='white-space:pre-wrap'>"+_esc(zh_sum)+"</div>")
        out.append('<div class="section"><h4>中文标题/摘要</h4>'+"".join(zh_parts)+'</div>')

    # 简短要点与详细全文解读分别展示。
    if digest_en or digest_zh:
        inner = ""
        if digest_en:
            inner += "<div style='white-space:pre-wrap'>" + _esc(digest_en) + "</div>"
        if digest_zh:
            inner += "<div style='white-space:pre-wrap;margin-top:8px'>" + _esc(digest_zh) + "</div>"
        out.append('<div class="section"><h4>Summary / 总结</h4>'+ inner +'</div>')

    full_zh = (sum_zh or {}).get("full_md") or ""
    full_en = (sum_en or {}).get("full_md") or ""
    if full_zh or full_en:
        notes = '<div class="section"><h4>Paper notes / 论文解读</h4>'
        source = it.get("fulltext_source") or {}
        if source:
            notes += (f'<p>来源：PDF 全文，共 {source["pages"]} 页；'
                      f'{source["text_pages"]} 页可提取文字</p>')
        if full_zh:
            notes += render_report(full_zh)
        if full_en:
            notes += render_report(full_en)
        out.append(notes + '</div>')

    out.append('</div>')
    return "\n".join(out)


def render_email_html(
    items: List[Dict[str, Any]],
    lang: str = "both",
    translations: Optional[Dict[str, Dict[str, str]]] = None,
    summaries_zh: Optional[Dict[str, Dict[str, str]]] = None,
    summaries_en: Optional[Dict[str, Dict[str, str]]] = None,
    detail: str = "full",
    max_items: int = 50,
    title: str = "arXiv Daily Digest",
) -> str:
    translations = translations or {}
    summaries_zh = summaries_zh or {}
    summaries_en = summaries_en or {}

    head = f"""
    <meta charset="utf-8">
    <div class="container">
      <h2 style="margin:8px 0 12px 0;">{_esc(title)}</h2>
      <style>{CSS}</style>
    """

    if not items:
        return head + "<p>No results.</p></div>"

    body = [head]
    for it in items[:max_items]:
        sid = it.get("id") or ""
        body.append(_render_card(it, translations.get(sid), summaries_zh.get(sid), summaries_en.get(sid)))
    body.append("</div>")
    return "\n".join(body)
