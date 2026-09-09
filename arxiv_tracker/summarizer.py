# -*- coding: utf-8 -*-
import logging
import os, re
from typing import Dict, Any, Optional, Tuple
from .llm import call_llm_two_stage
from .llm import call_llm_bilingual_summary

logger = logging.getLogger(__name__)

_BILINGUAL_SUMMARY_CACHE: Dict[Tuple[str, ...], Dict[str, str]] = {}
_SUMMARY_CACHE_LIMIT = 256


def _summary_cache_key(item: Dict[str, Any], cfg: Dict[str, Any]) -> Tuple[str, ...]:
    return tuple(str(value or "") for value in (
        item.get("id"),
        item.get("title"),
        item.get("summary"),
        item.get("comments"),
        item.get("venue_inferred") or item.get("journal_ref"),
        cfg.get("base_url"),
        cfg.get("model"),
        cfg.get("system_prompt_zh"),
        cfg.get("system_prompt_en"),
    ))

KNOWN_DATASETS = [
    "COCO","LVIS","ADE20K","Cityscapes","ScanNet","ImageNet","OpenImages",
    "Pascal VOC","NYUv2","KITTI","GQA","VQAv2","RefCOCO","RefCOCO+","RefCOCOg",
    "Flickr30k","SA-1B","LAION","Objects365","Waymo","nuScenes","COCO-Stuff"
]
TASK_HINTS = [
    ("open-vocabulary","Open-Vocabulary"),("open vocabulary","Open-Vocabulary"),
    ("segmentation","Segmentation"),("detection","Detection"),
    ("referring","Referring / Grounding"),("grounding","Grounding"),
    ("3d","3D Vision"),("multimodal","Vision-Language"),("vision-language","Vision-Language")
]

def _first_sentence(text: str, max_chars=1024):
    """取第一句，尽量不截断；超过 max_chars 时裁剪但不加 '…'。"""
    if not text: return ""
    t = re.sub(r"\s+", " ", text.strip())
    parts = re.split(r"(?<=[.!?。！？])\s+", t)
    pick = parts[0] if parts else t
    return pick[:max_chars]


def heuristic_paragraphs(item: Dict[str, Any]) -> Dict[str, str]:
    # 无 LLM 时的兜底：英文取摘要首句；中文为空（或直接复用摘要首句）
    absu = item.get("summary") or ""
    en = _first_sentence(absu) or (item.get("title") or "")
    zh = ""  # 如果你想兜底中文可放机器翻译，这里保持空即可
    return {"digest_en": en, "digest_zh": zh}
    
def _detect(items, text):
    T = (text or "").lower()
    out = []
    for n in items:
        if n.lower() in T and n not in out:
            out.append(n)
    return out[:8]

def _detect_tasks(text, title="", comments=""):
    src = " ".join([title or "", text or "", comments or ""]).lower()
    out = []
    for k, lab in TASK_HINTS:
        if k in src and lab not in out:
            out.append(lab)
    return out[:6]

def heuristic_two_stage(item: Dict[str, Any], lang: str, scope: str) -> Dict[str, str]:
    title   = item.get("title") or ""
    summ    = item.get("summary") or ""
    comm    = item.get("comments") or ""
    venue   = item.get("venue_inferred") or (item.get("journal_ref") or "")
    pdf     = item.get("pdf_url") or ""
    absu    = item.get("html_url") or ""
    codes   = item.get("code_urls") or []
    projs   = item.get("project_urls") or []

    tasks = _detect_tasks(summ, title, comm)
    datasets = _detect(KNOWN_DATASETS, summ + " " + comm)

    tldr = _first_sentence(summ) or title

    # —— 只保留关键信息；不再输出 Links（网页和邮件已有独立的链接区）——
    lines = []
    if tasks:    lines.append("- **Task / Problem**: " + ", ".join(tasks))
    lines.append("- **Core Idea**: " + (_first_sentence(summ, 360) or title))
    if datasets: lines.append("- **Data / Benchmarks**: " + ", ".join(datasets))
    if venue:    lines.append("- **Venue**: " + venue)

    card = "\n".join(lines)
    disc = "\n".join([
        "1. 相比强基线，优势是否稳定显著？",
        "2. 代价/延迟与内存开销如何，复现细节是否充分？",
        "3. 失败模式与局限？可能改进方向？",
        "4. 数据与指标是否充分支撑结论，是否存在偏置/重叠？",
        "5. 是否可迁移到真实应用或边缘设备？"
    ])
    full_md = "**Method Card (方法卡)**\n" + card + "\n\n**Discussion (讨论问题)**\n" + disc

    if scope == "tldr": full_md = ""
    if scope == "full": tldr = ""
    return {"tldr": tldr, "full_md": full_md}

def llm_two_stage(item: Dict[str, Any], lang: str, scope: str, cfg: Dict[str, Any]) -> Dict[str, str]:
    api_key = (cfg.get("api_key") or
               os.getenv(cfg.get("api_key_env") or "OPENAI_API_KEY", ""))
    if not api_key:
        raise RuntimeError("未找到 LLM API Key，请设置环境变量：{}，或在 config.yaml 的 llm.api_key 填入（仅本机测试用）"
                           .format(cfg.get("api_key_env") or "OPENAI_API_KEY"))
    return call_llm_two_stage(
        item=item, lang=lang, scope=scope,
        base_url=cfg.get("base_url", ""),
        model=cfg.get("model", ""),
        api_key=api_key,
        system_prompt=cfg.get("system_prompt_zh" if lang == "zh" else "system_prompt_en", "")
    )

def build_two_stage_summary(item: Dict[str, Any], mode: str, lang: str, scope: str, llm_cfg: Optional[Dict[str, Any]] = None):
    """
    兼容旧接口名，但现在输出：
      {"digest_en": "...", "digest_zh": "...", "tldr": "", "full_md": ""}
    其中 tldr/full_md 留空，供模板判断“不渲染卡片”
    """
    if mode == "llm":
        cfg = llm_cfg or {}
        api_key = (cfg.get("api_key") or os.getenv(cfg.get("api_key_env") or "OPENAI_API_KEY", ""))
        if api_key:
            try:
                cache_key = _summary_cache_key(item, cfg)
                data = _BILINGUAL_SUMMARY_CACHE.get(cache_key)
                if data is None:
                    data = call_llm_bilingual_summary(
                        item=item,
                        base_url=cfg.get("base_url", ""),
                        model=cfg.get("model", ""),
                        api_key=api_key,
                        system_prompt_zh=cfg.get("system_prompt_zh", ""),
                        system_prompt_en=cfg.get("system_prompt_en", "")
                    )
                    if len(_BILINGUAL_SUMMARY_CACHE) >= _SUMMARY_CACHE_LIMIT:
                        _BILINGUAL_SUMMARY_CACHE.pop(next(iter(_BILINGUAL_SUMMARY_CACHE)))
                    _BILINGUAL_SUMMARY_CACHE[cache_key] = dict(data)
                return {"digest_en": data.get("digest_en",""), "digest_zh": data.get("digest_zh",""), "tldr":"", "full_md":""}
            except Exception as exc:
                logger.exception(
                    "LLM summary failed for %s: %s",
                    item.get("id") or "<unknown>",
                    exc,
                )
        # LLM 不可用时兜底
        h = heuristic_paragraphs(item)
        return {"digest_en": h["digest_en"], "digest_zh": h["digest_zh"], "tldr":"", "full_md":""}

    # 非 llm 模式也输出同样结构（兜底）
    h = heuristic_paragraphs(item)
    return {"digest_en": h["digest_en"], "digest_zh": h["digest_zh"], "tldr":"", "full_md":""}
