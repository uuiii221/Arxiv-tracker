# -*- coding: utf-8 -*-
import os, re, sys, traceback, click
from dataclasses import replace
from .config import Settings
from .query import build_search_query
from .client import fetch_arxiv_feed
from .parser import parse_feed
from .output import save_json, save_markdown
from .summarizer import build_two_stage_summary, _BILINGUAL_SUMMARY_CACHE
from .llm import call_llm_translate
from .exporter import md_to_pdf
from .tracking import collect_papers, load_seen, save_seen, paper_key


def _split_categories(values):
    out = []
    for v in values or []:
        if not v:
            continue
        parts = re.split(r'\s*,\s*|\s*;\s*|/', v.strip())
        out.extend([p for p in parts if p])
    return out


def _split_keywords(values):
    out = []
    for v in values or []:
        if not v:
            continue
        parts = re.split(r'\s*,\s*|\s*;\s*', v.strip())
        out.extend([p for p in parts if p])
    return out


def _load_raw_cfg(maybe_path):
    import yaml
    path = maybe_path or "config.yaml"
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


@click.group()
def cli():
    """arxiv-tracker CLI"""
    pass


@cli.command("run")
@click.option("--config", "config_path", type=click.Path(exists=True), default="config.yaml", show_default=True, help="配置文件路径（YAML）")
@click.option("--categories", multiple=True, help="学科分类，可多次或逗号分隔")
@click.option("--keywords", multiple=True, help="关键词，可多次或逗号分隔")
@click.option("--exclude-keywords", multiple=True, help="排除关键词，可多次或逗号分隔") 
@click.option("--logic", type=click.Choice(["AND", "OR"], case_sensitive=False), default=None)
@click.option("--max-results", type=click.IntRange(min=1), default=None)
@click.option("--sort-by", type=click.Choice(["submittedDate", "lastUpdatedDate"]), default=None)
@click.option("--sort-order", type=click.Choice(["ascending", "descending"]), default=None)
@click.option("--lang", type=click.Choice(["zh", "en", "both"]), default=None, help="输出语言")
@click.option("--summary-mode", type=click.Choice(["none", "heuristic", "llm"]), default=None)
@click.option("--summary-scope", type=click.Choice(["tldr", "full", "both"]), default=None)
@click.option("--email", "email_enabled", is_flag=True, default=None, help="启用邮件发送（覆盖配置）")
@click.option("--email-detail", type=click.Choice(["simple", "full"]), default=None, help="邮件内容详略")
@click.option("--email-max-items", type=int, default=None, help="邮件最多包含的条目数")
@click.option("--out-dir", default="outputs", help="输出目录")
@click.option("--verbose", is_flag=True, help="打印详细运行日志")
@click.option("--translate", "translate_enabled", is_flag=True, default=None, help="启用 LLM 中文翻译（覆盖配置）")
@click.option("--translate-lang", type=click.Choice(["zh"]), default=None, help="翻译目标语言")
@click.option("--pdf", "pdf_enabled", is_flag=True, default=False, help="将 Markdown 同步导出为 PDF")
@click.option("--site-dir", default=None, help="输出静态站点目录（如 docs）")
@click.option("--site-url", default=None, help="站点首页 URL（用于邮件正文链接）")
@click.option("--no-email", is_flag=True, help="跳过邮件发送（用于重试）")
@click.option("--no-state-update", is_flag=True, help="预览时不更新已读状态，保留给后续正式推送")

def run(config_path, categories, keywords, exclude_keywords, logic, max_results, sort_by, sort_order,
        lang, summary_mode, summary_scope, email_enabled, email_detail, email_max_items,
        out_dir, verbose, translate_enabled, translate_lang, pdf_enabled, no_email: bool, no_state_update: bool,
        site_dir, site_url):
    try:
        if verbose:
            click.echo("[Run] Start")

        # 1) 载入设置
        cfg = Settings.from_file(config_path) if config_path else Settings()
        cats = _split_categories(categories)
        keys = _split_keywords(keywords)
        ex_keys = _split_keywords(exclude_keywords)
        cfg.merge_cli(categories=cats or None,
                      keywords=keys or None,
                      exclude_keywords=ex_keys or None,
                      logic=(logic or cfg.logic),
                      max_results=(max_results or cfg.max_results),
                      sort_by=(sort_by or cfg.sort_by),
                      sort_order=(sort_order or cfg.sort_order))

        raw_cfg = _load_raw_cfg(config_path)
        lang = lang or raw_cfg.get("lang", "both")

        # 摘要
        summary_cfg = raw_cfg.get("summary", {}) or {}
        llm_cfg = raw_cfg.get("llm", {}) or {}
        mode = summary_mode or summary_cfg.get("mode", "none")
        scope = summary_scope or summary_cfg.get("scope", "both")
        summary_source = summary_cfg.get("source", "abstract")
        if summary_source not in ("abstract", "fulltext"):
            raise ValueError("summary.source must be abstract or fulltext")
        use_fulltext = mode == "llm" and summary_source == "fulltext"
        fulltext_cfg = raw_cfg.get("fulltext") or {}
        continue_fulltext_errors = bool(fulltext_cfg.get("continue_on_error", True))
        extra_candidates = max(0, int(fulltext_cfg.get("max_extra_candidates", 5))) if use_fulltext and continue_fulltext_errors else 0
        _BILINGUAL_SUMMARY_CACHE.clear()  # 缓存仅在一次运行内共享，不跨日缓存失败
        strict_llm = bool(llm_cfg.get("fail_on_error", False))

        # 翻译
        trans_cfg = (raw_cfg.get("translate", {}) or {}).copy()
        if translate_enabled is not None:
            trans_cfg["enabled"] = translate_enabled
        if translate_lang:
            trans_cfg["lang"] = translate_lang
        if "fields" not in trans_cfg:
            trans_cfg["fields"] = ["title", "summary"]
        api_key = (llm_cfg.get("api_key")
                   or os.getenv(llm_cfg.get("api_key_env") or "OPENAI_API_KEY", ""))
        if strict_llm and (mode == "llm" or trans_cfg.get("enabled")) and not api_key:
            raise RuntimeError("Missing LLM API key: set {}".format(
                llm_cfg.get("api_key_env") or "OPENAI_API_KEY"))

        # —— 邮件配置（合并 config + CLI 覆盖 + no-email）——
        email_cfg = (raw_cfg.get("email", {}) or {}).copy()
        if email_enabled is not None:
            email_cfg["enabled"] = bool(email_enabled)
        if email_detail:
            email_cfg["detail"] = email_detail
        if email_max_items is not None:
            email_cfg["max_items"] = int(email_max_items)
        # no-email 优先级最高
        if no_email:
            email_cfg["enabled"] = False
        # 兜底默认值
        email_cfg.setdefault("enabled", False)
        email_cfg.setdefault("detail", "full")
        email_cfg.setdefault("max_items", 50)

        # —— Freshness & 去重配置 —— #
        fresh_cfg = (raw_cfg.get("freshness") or {})
        since_days = int(fresh_cfg.get("since_days", 0) or 0)          # 近 N 天（0=不启用）
        unique_only = bool(fresh_cfg.get("unique_only", False))        # 跨天去重
        state_path = fresh_cfg.get("state_path", ".state/seen.json")   # 去重状态文件
        fallback_when_empty = bool(fresh_cfg.get("fallback_when_empty", False))
        if verbose:
            click.echo("[Freshness] since_days={}, unique_only={}, state_path='{}', fallback_when_empty={}"
                       .format(since_days, unique_only, state_path, fallback_when_empty))

        if verbose:
            click.echo("[Run] categories: {}".format(cfg.categories))
            click.echo("[Run] keywords  : {}".format(cfg.keywords))
            click.echo("[Run] required keyword groups: {}".format(cfg.keyword_groups))
            click.echo("[Run] summary   : {}/{}".format(mode, scope))
            click.echo("[Run] lang      : {}".format(lang))
            click.echo("[Run] translate : {} -> {}".format(trans_cfg.get("enabled", False),
                                                          trans_cfg.get("lang", "zh")))
            click.echo("[Run] email     : enabled={}, detail={}, max_items={}".format(
                email_cfg.get("enabled", False), email_cfg.get("detail"), email_cfg.get("max_items")
            ))

        # 2) 查询（分页抓取直到攒够“未读新条目”或触达时间窗）
        q = build_search_query(cfg.categories, cfg.keywords, cfg.exclude_keywords, cfg.logic,
                               keyword_groups=cfg.keyword_groups)
        click.echo("[Query] {}".format(q))

        # 读取已见集合（兼容 list / {"ids":[...]} / {id: timestamp} 三种格式）
        seen_ids = load_seen(state_path) if unique_only else set()
        selection_cfg = replace(cfg, max_results=cfg.max_results + extra_candidates)
        collected = collect_papers(q, selection_cfg, seen_ids, since_days=since_days, unique_only=unique_only)

        # Fallback：若空且允许回退，则给最新一页（不考虑去重/时间窗）
        if not collected and fallback_when_empty:
            xml = fetch_arxiv_feed(
                q, start=0, max_results=selection_cfg.max_results,
                sort_by=cfg.sort_by, sort_order=cfg.sort_order
            )
            collected = parse_feed(xml) or []

        items = collected
        if not items:
            click.secho("[Info] No new items after pagination/freshness/dedup filter.", fg="yellow")
        else:
            click.echo(f"[Info] Fetched {len(items)} candidate(s); targeting up to {cfg.max_results} paper(s).")

        # 先从已有文本/HTML提取；若仍没有再扫 PDF 头部兜底
        scrape_cfg = (raw_cfg.get("scrape") or {})
        scrape_html = bool(scrape_cfg.get("html", True))
        scrape_pdf_if_missing = bool(scrape_cfg.get("pdf_if_missing", True))
        scrape_pdf_always = bool(scrape_cfg.get("pdf_first_page", False))
        if use_fulltext:
            # Full PDF is read below; reuse its text instead of downloading the head again.
            scrape_pdf_if_missing = scrape_pdf_always = False
        scrape_to = int(scrape_cfg.get("timeout", 10))

        from .extrascrape import augment_item_links

        if verbose:
            click.echo(f"[Scrape] html={scrape_html} pdf_if_missing={scrape_pdf_if_missing} "
                       f"pdf_first_page={scrape_pdf_always} timeout={scrape_to}")

        for it in items:
            try:
                added = augment_item_links(
                    it,
                    html=scrape_html,
                    pdf_if_missing=scrape_pdf_if_missing,
                    pdf_first_page=scrape_pdf_always,
                    timeout=scrape_to,
                )
                if verbose and added > 0:
                    click.echo(f"[Scrape] +{added} code link(s) for {(it.get('id') or '')[:32]}")
            except Exception as e:
                click.secho(f"[Scrape] 补链失败 {(it.get('id') or '')[:18]}...: {e}", fg="yellow")

        # 3) 逐篇读取完整 PDF，不以首页/摘要代替全文。
        if use_fulltext:
            from .fulltext import FullTextError, load_paper_fulltext
            ready, failures = [], []
            for it in items:
                click.echo(f"[FullText] Reading {it.get('id')}")
                try:
                    it["_fulltext"], it["fulltext_source"] = load_paper_fulltext(it, fulltext_cfg)
                except FullTextError as exc:
                    if not continue_fulltext_errors:
                        raise
                    failures.append({"id": it.get("id"), "title": it.get("title"), "error": str(exc)})
                    click.secho(f"[FullText] Skipped {it.get('id')}: {exc}; remains unread", fg="yellow")
                    continue
                augment_item_links(it, html=False, pdf_if_missing=False)
                info = it["fulltext_source"]
                click.echo(f"[FullText] Parsed {info['text_pages']}/{info['pages']} pages")
                ready.append(it)
                if len(ready) >= cfg.max_results:
                    break
            from pathlib import Path
            failure_path = Path(out_dir) / "fulltext_failures.json"
            if failures or failure_path.exists():
                import json
                failure_path.parent.mkdir(parents=True, exist_ok=True)
                failure_path.write_text(json.dumps(failures, ensure_ascii=False, indent=2), encoding="utf-8")
                if failures:
                    click.echo(f"[FullText] Saved skipped-paper diagnostics: {failure_path}")
            if items and not ready:
                raise FullTextError(f"No readable full text among {len(items)} candidate(s); dedup state was not advanced")
            items = ready
            if items:
                click.echo(f"[FullText] Ready: {len(items)}/{cfg.max_results} paper(s); skipped {len(failures)}")

        # 每篇独立生成所选语言的解读；双语模式共用一次模型调用。
        summaries_zh, summaries_en = {}, {}
        def _sum_for_lang(L):
            out = {}
            for it in items:
                sid = it.get("id") or ""
                if L == "zh" or lang == "en":
                    click.echo(f"[Summary] Generating notes for {sid}")
                out[sid] = build_two_stage_summary(item=it, mode=mode, lang=L, scope=scope, llm_cfg=llm_cfg,
                                                  source=summary_source, summary_cfg=summary_cfg, output_lang=lang)
                if strict_llm and mode == "llm" and out[sid].get("_llm_failed"):
                    raise RuntimeError("LLM summary failed for {}; dedup state was not advanced".format(sid))
            return out

        if lang in ("zh", "both"):
            summaries_zh = _sum_for_lang("zh")
        if lang in ("en", "both"):
            summaries_en = _sum_for_lang("en")

        # 4) 翻译（中文）
        translations = {}
        if trans_cfg.get("enabled") and (trans_cfg.get("lang", "zh") == "zh"):
            api_key = (llm_cfg.get("api_key")
                       or os.getenv(llm_cfg.get("api_key_env") or "OPENAI_API_KEY", ""))
            if not api_key:
                click.secho("[Translate] 跳过：未找到 LLM API Key（配置 llm.api_key 或设置环境变量 {}）"
                            .format(llm_cfg.get("api_key_env") or "OPENAI_API_KEY"), fg="yellow")
            else:
                for it in items:
                    sid = it.get("id") or ""
                    try:
                        translations[sid] = call_llm_translate(
                            item=it, target_lang="zh",
                            base_url=llm_cfg.get("base_url", ""),
                            model=llm_cfg.get("model", ""),
                            api_key=api_key,
                            system_prompt=llm_cfg.get("system_prompt_translate_zh", "")
                        )
                    except Exception as e:
                        click.secho(f"[Translate] 失败 {sid[:18]}...: {e}", fg="red")
                        if strict_llm:
                            raise

        # 5) 终端预览
        if not items:
            click.echo("（今日暂无新增）")
        for idx, it in enumerate(items, 1):
            title = it.get("title", "")
            venue = it.get("venue_inferred") or (it.get("journal_ref") or "")
            click.echo(f"{idx:02d}. {title}  [{' / '.join(it.get('authors', []))}]")
            if venue:
                click.echo(f"    Venue: {venue}")
            click.echo(f"    Time: {it.get('published', '—')}  ->  {it.get('updated', '—')}")
            if it.get("pdf_url"):
                click.echo(f"    PDF : {it['pdf_url']}")
            sid = it.get("id") or ""
            s = (summaries_zh.get(sid) or summaries_en.get(sid) or {})
            if s.get("tldr"):
                click.echo(f"    TL;DR: {s['tldr']}")
            tx = translations.get(sid)
            if tx and tx.get("title_zh"):
                click.echo(f"    标题(中): {tx['title_zh']}")
            click.echo("")

        # 6) 保存到文件 + 生成 PDF（可选）
        # 原始全文只用于本次推理，不随公开的 outputs/JSON 上传。
        for it in items:
            it.pop("_fulltext", None)
        json_path = save_json(items, out_dir, summaries_zh=summaries_zh, summaries_en=summaries_en)
        md_path   = save_markdown(items, out_dir, summaries_zh, summaries_en, lang=lang, translations=translations)
        click.echo(f"Saved: {json_path}")
        click.echo(f"Saved: {md_path}")

        # 6.5) 生成站点（如启用）
        page_url = None
        try:
            from .sitegen import generate_site
            site_cfg = (raw_cfg.get("site") or {}) if 'raw_cfg' in locals() else {}
            sd = site_dir or site_cfg.get("dir")
            if sd and (site_cfg.get("enabled", False) or site_dir is not None):
                keep = int(site_cfg.get("keep_runs", 60))
                title = site_cfg.get("title", "arXiv Results")
                theme = site_cfg.get("theme", "light")
                accent = site_cfg.get("accent", "#2563eb")
                site_res = generate_site(
                    items=items,
                    summaries_zh=summaries_zh or {},
                    summaries_en=summaries_en or {},
                    translations=translations or {},
                    site_dir=sd, site_title=title, keep_runs=keep,
                    theme=theme, accent=accent
                )
                click.echo(f"Saved: {site_res['index_path']}")
                page_url = site_url or site_cfg.get("url")
                if page_url and not page_url.endswith("/"):
                    page_url += "/"
        except Exception as e:
            raise RuntimeError(f"[Site] 生成失败: {e}") from e

        pdf_path = ""
        if pdf_enabled or (email_cfg.get("enabled") and email_cfg.get("attach_pdf")):
            try:
                pdf_path = md_to_pdf(md_path)
                click.echo(f"Saved: {pdf_path}")
            except Exception as e:
                raise RuntimeError(f"[PDF] 生成失败: {e}; install requirements-pdf.txt") from e

        # 7) Only deliver nonempty digests. A stable content flag survives retries.
        if email_cfg.get("enabled") and items:
            from .delivery import send_digest_email
            send_digest_email(
                items=items, email_cfg=email_cfg, out_dir=out_dir, md_path=md_path,
                pdf_path=pdf_path, page_url=page_url, lang=lang, translations=translations,
                summaries_zh=summaries_zh, summaries_en=summaries_en,
            )
        elif email_cfg.get("enabled"):
            click.echo("[Email] No new papers; skipped empty email")

        # 8) Advance state only after every enabled destination succeeded.
        if unique_only and state_path and items and not no_state_update:
            save_seen(state_path, seen_ids | {paper_key(it["id"]) for it in items})
            click.echo(f"[Freshness] Updated dedup state -> {state_path}")

        if verbose:
            click.echo("[Run] Done")

    except Exception as e:
        click.secho("[Run] ERROR: {}".format(e), fg="red")
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    cli()
