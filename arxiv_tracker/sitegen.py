# -*- coding: utf-8 -*-
import os, datetime, html
from typing import Dict, List, Any, Optional
from .rendering import render_report
from .figures import copy_site_figures, render_figures_html

import re
try:
    from markdown import markdown as _md
except Exception:
    _md = None

def _esc(x):  # 保留你的实现
    import html
    return html.escape(x or "", quote=True)

def _md2html(md: str) -> str:
    if not md: return ""
    if _md:
        return _md(md, extensions=["extra", "sane_lists", "tables"])
    return "<pre class='mono'>" + _esc(md) + "</pre>"

# --- 语言/内容判断 & 文本处理 ---
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
def _has_cjk(s: str) -> bool:
    return bool(_CJK_RE.search(s or ""))

def _first_sentence(text: str) -> str:
    if not text: return ""
    t = re.sub(r"\s+", " ", text.strip())
    parts = re.split(r"(?<=[。！？.!?])\s+", t)
    return parts[0] if parts else t

def _strip_format(md: str) -> str:
    """
    去掉冗余行：**Method Card...**, **Discussion...**, **Links**...
    """
    if not md: return ""
    out = []
    for line in md.splitlines():
        L = line.strip().lower()
        if L.startswith("**method card") or L.startswith("**discussion"):
            continue
        if L.startswith("- **links**"):
            continue
        out.append(line)
    return "\n".join(out)

def _localize_md_to_zh(md: str) -> str:
    """
    仅把标签本地化，内容不硬翻译（避免引入错误）。英文值保留。
    """
    repl = {
        "**Task / Problem**:": "**任务 / 问题**：",
        "**Core Idea**:": "**核心思路**：",
        "**Data / Benchmarks**:": "**数据 / 基准**：",
        "**Venue**:": "**会议 / 期刊**：",
    }
    s = md
    for k, v in repl.items():
        s = s.replace(k, v)
    return s
    
try:
    # 用于把 full_md 渲染成真正的 HTML 列表/加粗等
    from markdown import markdown as _md
except Exception:
    _md = None

def _esc(x: Optional[str]) -> str:
    return html.escape(x or "", quote=True)

def _md2html(md: str) -> str:
    if not md: return ""
    if _md:
        return _md(md, extensions=["extra", "sane_lists", "tables"])
    # 兜底（没有 markdown 包时，退化为等宽块）
    return "<pre class='mono'>" + _esc(md) + "</pre>"

def _strip_redundant_links(md: str) -> str:
    out = []
    for line in (md or "").splitlines():
        if line.strip().lower().startswith("- **links**"):
            continue
        out.append(line)
    return "\n".join(out)

def _css(accent: str = "#2563eb") -> str:
    return f"""
:root {{
  --bg:#f8fafc; --card:#ffffff; --text:#0f172a; --muted:#667085; --border:#e5e7eb; --acc:{accent};
}}
:root[data-theme="dark"] {{
  --bg:#0b0f17; --card:#111827; --text:#e5e7eb; --muted:#9ca3af; --border:#1f2937; --acc:{accent};
}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--text);
  font-family:ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,Helvetica,Arial;line-height:1.6;}}
.container{{max-width:900px;margin:0 auto;padding:18px;}}
.header{{display:flex;gap:10px;justify-content:space-between;align-items:center;margin:8px 0 16px;flex-wrap:wrap}}
h1{{font-size:22px;margin:0}}
.badge{{font-size:12px;color:#111827;background:var(--acc);padding:2px 8px;border-radius:999px}}
.card{{background:var(--card);border:1px solid var(--border);border-radius:16px;padding:16px 18px;margin:14px 0;box-shadow:0 1px 2px rgba(0,0,0,.04)}}
.title{{font-weight:700;margin:0 0 6px 0;font-size:18px}}
.meta-line{{color:var(--muted);font-size:13px;margin:2px 0}}
.links a{{color:var(--acc);text-decoration:none;margin-right:12px}}
.detail{{margin-top:10px;background:rgba(2,6,23,.03);border:1px solid var(--border);border-radius:10px;padding:8px 10px}}
summary{{cursor:pointer;color:var(--acc)}}
.mono{{white-space:pre-wrap;background:rgba(2,6,23,.03);border:1px solid var(--border);padding:10px;border-radius:10px}}
.row{{display:grid;grid-template-columns:1fr;gap:12px}}
@media (min-width: 860px) {{
  .row-2{{grid-template-columns:1fr 1fr}}
}}
.footer{{color:var(--muted);font-size:13px;margin:20px 0 10px}}
.hr{{height:1px;background:var(--border);margin:14px 0}}
.history-list a{{display:block;color:var(--acc);text-decoration:none;margin:4px 0}}
.controls{{display:flex;gap:8px;align-items:center}}
.btn{{border:1px solid var(--border);background:var(--card);padding:6px 10px;border-radius:10px;cursor:pointer;color:var(--text)}}
.btn:hover{{border-color:var(--acc)}}
"""

def _join_links(it: Dict[str, Any]) -> str:
    parts = []
    if it.get("html_url"): parts.append(f'<a href="{_esc(it["html_url"])}">Abs</a>')
    if it.get("pdf_url"):  parts.append(f'<a href="{_esc(it["pdf_url"])}">PDF</a>')
    if it.get("code_urls"):
        for i,u in enumerate(it["code_urls"][:3]): parts.append(f'<a href="{_esc(u)}">Code{i+1}</a>')
    if it.get("project_urls"):
        for i,u in enumerate(it["project_urls"][:2]): parts.append(f'<a href="{_esc(u)}">Project{i+1}</a>')
    return " · ".join(parts)

def _card(it: Dict[str, Any],
          trans_zh: Optional[Dict[str,str]],
          sum_zh: Optional[Dict[str,str]],
          sum_en: Optional[Dict[str,str]], figure_prefix="assets/figures/") -> str:
    t = it.get("title") or ""
    au = ", ".join(it.get("authors") or [])
    venue = it.get("venue_inferred") or (it.get("journal_ref") or "")
    pub = it.get("published") or "—"
    upd = it.get("updated") or "—"
    comm = it.get("comments") or ""
    absu = it.get("summary") or ""

    zh_title = (trans_zh or {}).get("title_zh")
    zh_abs   = (trans_zh or {}).get("summary_zh")

    # 显示所选语言的要点。
    digest_en = (sum_en or {}).get("digest_en") or (sum_zh or {}).get("digest_en") or ""
    digest_zh = (sum_zh or {}).get("digest_zh") or (sum_en or {}).get("digest_zh") or ""

    parts = [f'<div class="card">', f'<div class="title">{_esc(t)}</div>']

    # 元信息分行
    parts.append(f'<div class="meta-line">Authors: {_esc(au)}</div>')
    if venue:
        parts.append(f'<div class="meta-line">Venue: {_esc(venue)}</div>')
    parts.append(f'<div class="meta-line">First: {_esc(pub)} · Latest: {_esc(upd)}</div>')
    if comm:
        parts.append(f'<div class="meta-line">Comments: {_esc(comm)}</div>')

    # 链接
    links = _join_links(it)
    if links: parts.append(f'<div class="links" style="margin-top:8px">{links}</div>')

    # 摘要（英文原文，可折叠）
    if absu:
        parts.append('<details class="detail"><summary>Abstract</summary>')
        parts.append(f'<div class="mono">{_esc(absu)}</div></details>')

    # 中文标题/摘要（可选）
    if zh_abs or zh_title:
        parts.append('<details class="detail"><summary>中文标题/摘要</summary>')
        if zh_title: parts.append(f'<div class="mono"><b>标题：</b>{_esc(zh_title)}</div>')
        if zh_abs:   parts.append(f'<div class="mono" style="margin-top:8px">{_esc(zh_abs)}</div>')
        parts.append('</details>')

    # 简短要点与详细全文解读分别展示。
    if digest_en or digest_zh:
        label = "总结" if digest_zh and not digest_en else "Summary / 总结"
        parts.append(f'<details class="detail"><summary>{label}</summary>')
        if digest_en:
            parts.append(f'<div class="mono">{_esc(digest_en)}</div>')
        if digest_zh:
            parts.append(f'<div class="mono" style="margin-top:8px">{_esc(digest_zh)}</div>')
        parts.append('</details>')

    full_zh = (sum_zh or {}).get("full_md") or ""
    full_en = (sum_en or {}).get("full_md") or ""
    if full_zh or full_en:
        label = "论文解读" if full_zh and not full_en else "Paper notes / 论文解读"
        parts.append(f'<details class="detail" open><summary>{label}</summary>')
        source = it.get("fulltext_source") or {}
        if source:
            parts.append(f'<div class="meta-line">来源：PDF 全文，共 {source["pages"]} 页；'
                         f'{source["text_pages"]} 页可提取文字</div>')
        parts.append(render_figures_html(it, prefix=figure_prefix))
        if full_zh:
            parts.append('<div class="reading-notes">' + render_report(full_zh) + '</div>')
        if full_en:
            parts.append('<div class="reading-notes">' + render_report(full_en) + '</div>')
        parts.append('</details>')

    parts.append('</div>')
    return "\n".join(parts)


def _write(path: str, text: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f: f.write(text)

def _build_page(title: str, sub: str, cards_html: str, history_html: str,
                theme_mode: str, accent: str) -> str:
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    js = f"""
<script>
(function() {{
  const root = document.documentElement;
  function apply(t) {{
    if (t==='dark') root.setAttribute('data-theme','dark');
    else if (t==='light') root.removeAttribute('data-theme');
    else {{
      if (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches)
        root.setAttribute('data-theme','dark');
      else root.removeAttribute('data-theme');
    }}
  }}
  let t = localStorage.getItem('theme') || '{theme_mode}';
  if (!['light','dark','auto'].includes(t)) t='light';
  apply(t);
  window.__toggleTheme = function() {{
    let cur = localStorage.getItem('theme') || '{theme_mode}';
    if (cur==='light') cur='dark';
    else if (cur==='dark') cur='auto';
    else cur='light';
    localStorage.setItem('theme', cur);
    apply(cur);
    const el=document.getElementById('theme-label');
    if(el) el.textContent = cur.toUpperCase();
  }}
  window.__expandAll = function(open) {{
    document.querySelectorAll('details').forEach(d => d.open = !!open);
  }}
}})();
</script>
"""
    controls = """
<div class="controls">
  <button class="btn" onclick="__toggleTheme()">Theme: <span id="theme-label" style="margin-left:6px">AUTO</span></button>
  <button class="btn" onclick="__expandAll(true)">Expand All</button>
  <button class="btn" onclick="__expandAll(false)">Collapse All</button>
</div>
"""
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_esc(title)}</title><style>{_css(accent)}</style>{js}</head>
<body>
  <div class="container">
    <div class="header">
      <h1>{_esc(title)}</h1>
      <div style="display:flex;gap:10px;align-items:center">
        {controls}
        <span class="badge">{_esc(now)}</span>
      </div>
    </div>
    <div class="hr"></div>
    <div>{_esc(sub)}</div>
    <div class="row">{cards_html}</div>
    <details style="margin-top:16px" class="detail"><summary>History</summary>
      <div class="history-list">{history_html}</div>
    </details>
    <div class="footer">Generated by arxiv-tracker</div>
  </div>
</body></html>
"""

def _history_list(archive_dir: str, keep: int) -> List[str]:
    if not os.path.isdir(archive_dir):
        return []
    files = [f for f in os.listdir(archive_dir) if f.endswith(".html")]
    files.sort(reverse=True)
    files = files[:keep]
    links = []
    for f in files:
        date = f.replace(".html","")
        links.append(f'<a href="archive/{_esc(f)}">{_esc(date)}</a>')
    return links

def generate_site(items: List[Dict[str,Any]],
                  summaries_zh: Dict[str,Dict[str,str]],
                  summaries_en: Dict[str,Dict[str,str]],
                  translations: Dict[str,Dict[str,str]],
                  site_dir: str, site_title: str = "arXiv Results",
                  keep_runs: int = 60,
                  theme: str = "light",
                  accent: Optional[str] = None) -> Dict[str,str]:
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M")
    archive_dir = os.path.join(site_dir, "archive")
    os.makedirs(archive_dir, exist_ok=True)
    open(os.path.join(site_dir, ".nojekyll"), "w").close()

    copy_site_figures(items, site_dir)
    cards, archive_cards = [], []
    for it in items:
        sid = it.get("id") or ""
        cards.append(_card(it, translations.get(sid), summaries_zh.get(sid), summaries_en.get(sid)))
        archive_cards.append(_card(it, translations.get(sid), summaries_zh.get(sid), summaries_en.get(sid),
                                   figure_prefix="../assets/figures/"))
    cards_html = "\n".join(cards)
    hist_html = "\n".join(_history_list(archive_dir, keep_runs))

    acc = (accent or "#2563eb").strip()

    arch_html = _build_page(site_title, f"Snapshot: {stamp}", "\n".join(archive_cards), history_html=hist_html,
                            theme_mode=theme, accent=acc)
    arch_path = os.path.join(archive_dir, f"{stamp}.html")
    _write(arch_path, arch_html)

    index_html = _build_page(site_title, "Latest digest", cards_html, history_html=hist_html,
                             theme_mode=theme, accent=acc)
    index_path = os.path.join(site_dir, "index.html")
    _write(index_path, index_html)

    return {"index_path": index_path, "archive_path": arch_path, "stamp": stamp}
