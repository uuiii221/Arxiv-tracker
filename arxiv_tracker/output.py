# -*- coding: utf-8 -*-
import os, json, datetime
from typing import List, Dict, Any, Optional

def _ensure_dir(p: str):
    os.makedirs(p, exist_ok=True)

def save_json(items: List[Dict[str, Any]], out_dir: str) -> str:
    _ensure_dir(out_dir)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(out_dir, f"arxiv_{ts}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)
    return path

def _render_lang_block(lang_label: str, it: Dict[str, Any],
                       summ: Optional[Dict[str, str]],
                       trans: Optional[Dict[str, str]]):
    lines = []
    lines.append(f"### [{lang_label}]")
    # 翻译优先展示（如有）
    if trans:
        t_title = trans.get("title_zh")
        t_sum   = trans.get("summary_zh")
        if t_title or t_sum:
            lines.append("**中文翻译**")
            if t_title: lines.append(f"- 标题：{t_title}")
            if t_sum:   lines.append(f"- 摘要：{t_sum}")
            lines.append("")
    if summ and summ.get("tldr"):
        lines.append("> **TL;DR**: " + summ["tldr"])
        lines.append("")
    if summ:
        digest = summ.get("digest_zh" if lang_label == "中文" else "digest_en")
        if digest:
            lines.append(digest)
            lines.append("")
    if summ and summ.get("full_md"):
        lines.append(summ["full_md"])
        lines.append("")
    return lines

def save_markdown(items: List[Dict[str, Any]], out_dir: str,
                  summaries_zh: Dict[str, Dict[str, str]] = None,
                  summaries_en: Dict[str, Dict[str, str]] = None,
                  lang: str = "both",
                  translations: Dict[str, Dict[str, str]] = None) -> str:
    _ensure_dir(out_dir)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(out_dir, f"arxiv_{ts}.md")
    lines = ["# arXiv 检索结果 / Results", ""]
    for i, it in enumerate(items, 1):
        au = ", ".join(it.get("authors", []))
        title = it.get("title", "")
        venue = it.get("venue_inferred") or (it.get("journal_ref") or "")
        pub = it.get("published", "")
        upd = it.get("updated", "")
        lines.append(f"## {i}. {title}")
        lines.append(f"- Authors：{au}")
        if venue:
            lines.append(f"- Venue：{venue}")
        if it.get("comments"):
            lines.append(f"- Comments：{it['comments']}")
        lines.append(f"- First：{pub or '—'}；Latest：{upd or '—'}")
        if it.get("html_url"):
            lines.append(f"- Abs：{it['html_url']}")
        if it.get("pdf_url"):
            lines.append(f"- PDF：{it['pdf_url']}")
        if it.get("code_urls"):
            lines.append(f"- Code：{', '.join(it['code_urls'])}")
        if it.get("project_urls"):
            lines.append(f"- Project：{', '.join(it['project_urls'])}")

        sid = it.get("id") or ""
        trans = translations.get(sid) if translations else None
        if lang in ("zh", "both"):
            lines.extend(_render_lang_block("中文", it, (summaries_zh or {}).get(sid), trans))
        if lang in ("en", "both"):
            lines.extend(_render_lang_block("English", it, (summaries_en or {}).get(sid), None))
        lines.append("")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return path
