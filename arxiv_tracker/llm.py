# -*- coding: utf-8 -*-
import os, json, re, requests
from typing import Dict, Any, List

# ========== 通用小工具 ==========

def _json_loose(s: str) -> Dict[str, Any]:
    """
    宽松 JSON 解析：尽力从文本中抽出首个 {...} 为 JSON。
    """
    decoder = json.JSONDecoder()
    candidates = (s, re.sub(r",\s*([}\]])", r"\1", s))
    for candidate in candidates:
        for match in re.finditer(r"\{", candidate):
            try:
                value, _ = decoder.raw_decode(candidate[match.start():])
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                return value
    return {}

def _loose_json_load(s: str) -> Dict[str, Any]:
    """兼容旧名，等价 _json_loose。"""
    return _json_loose(s)

def _require_nonempty_strings(data: Dict[str, Any], fields: List[str]) -> Dict[str, str]:
    missing = [
        field for field in fields
        if not isinstance(data.get(field), str) or not data[field].strip()
    ]
    if missing:
        raise ValueError("LLM JSON response has empty required fields: {}".format(", ".join(missing)))
    return {field: data[field].strip() for field in fields}

def _normalize_chat_endpoint(base_url: str) -> str:
    """
    允许三种写法：
      1) https://api.xxx.com
      2) https://api.xxx.com/v1
      3) https://api.xxx.com/v1/chat/completions
    统一规范到完整终点：.../v1/chat/completions
    """
    if not base_url:
        raise ValueError("llm.base_url is empty")
    base = base_url.rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    if base.endswith("/v1"):
        return base + "/chat/completions"
    return base + "/v1/chat/completions"

def _chat_completions_request(
    *,
    base_url: str,
    api_key: str,
    model: str,
    messages: List[Dict[str, str]],
    temperature: float = 0.2,
    max_tokens: int = 1024,
    timeout: int = 30,
    json_object: bool = False,
    disable_thinking: bool = False,
) -> str:
    """
    统一的 OpenAI 兼容 Chat Completions 请求（requests 直连）。
    适配 DeepSeek / SiliconFlow / 其他 OAI 兼容服务。
    """
    url = _normalize_chat_endpoint(base_url)
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    if json_object:
        payload["response_format"] = {"type": "json_object"}
    if disable_thinking and model.lower().startswith("deepseek-v4"):
        payload["thinking"] = {"type": "disabled"}
    for attempt in range(3):
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=timeout)
            resp.raise_for_status()
            break
        except requests.Timeout:
            if attempt == 2:
                raise
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else None
            if status != 429 and not (status is not None and 500 <= status < 600):
                raise
            if attempt == 2:
                raise
    data = resp.json()

    # 标准 OAI 兼容返回
    try:
        return data["choices"][0]["message"]["content"]
    except Exception:
        # 兜底：部分实现把文本放在 text
        return data.get("choices", [{}])[0].get("text", "")

# ========== 双语“一段话总结” ==========

def call_llm_bilingual_summary(
    item: Dict[str, Any],
    *,
    base_url: str,
    model: str,
    api_key: str,
    system_prompt_zh: str = "",
    system_prompt_en: str = ""
) -> Dict[str, str]:
    """
    让 LLM 直接输出两段“总结”：digest_en / digest_zh
    内容要求：动机、方法、实验结果（各 1-2 句，合成两段：英文一段 + 中文一段）
    —— 统一 OpenAI 兼容通道，无需区分供应商。
    """
    title   = item.get("title") or ""
    summary = item.get("summary") or ""
    comments= item.get("comments") or ""
    venue   = item.get("venue_inferred") or (item.get("journal_ref") or "")

    sys_prompt = system_prompt_en or "You are a precise academic assistant. Summarize papers concisely."

    user_payload = {
        "title": title,
        "abstract": summary,
        "venue_or_comments": (venue or comments or "")
    }

    messages = [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content":
            "Given the paper metadata below, write TWO concise one-paragraph digests:\n"
            "1) English paragraph first.\n"
            "2) Then a Simplified Chinese paragraph.\n"
            "- Each paragraph must briefly cover: motivation, method, and main experimental results.\n"
            "- Do not include links, bullet lists, markdown, or headings. Plain sentences only.\n"
            '- Return STRICT JSON: {\"digest_en\": \"...\", \"digest_zh\": \"...\"}\n\n'
            f"DATA:\n{json.dumps(user_payload, ensure_ascii=False)}"
        }
    ]

    text = _chat_completions_request(
        base_url=base_url, api_key=api_key, model=model, messages=messages,
        temperature=0.2, max_tokens=600,
        json_object=True, disable_thinking=True
    )
    data = _json_loose(text)
    required = _require_nonempty_strings(data, ["digest_en", "digest_zh"])
    return {
        "digest_en": required["digest_en"],
        "digest_zh": required["digest_zh"],
    }

# ========== 两阶段摘要（保留你原有接口与行为） ==========

def build_llm_prompt(item: Dict[str, Any], lang: str = "zh", scope: str = "both"):
    title   = item.get("title") or ""
    authors = ", ".join(item.get("authors") or [])
    venue   = item.get("venue_inferred") or (item.get("journal_ref") or "")
    comments = item.get("comments") or ""
    summary  = item.get("summary") or ""
    links = {
        "html": item.get("html_url"),
        "pdf": item.get("pdf_url"),
        "code": item.get("code_urls") or [],
        "project": item.get("project_urls") or [],
        "other": item.get("other_urls") or [],
    }
    meta = {
        "title": title, "authors": authors, "venue": venue,
        "comments": comments, "summary": summary, "links": links
    }
    ask_lang = "中文" if lang == "zh" else "English"
    user_prompt = f"""
请阅读以下论文元信息(JSON)，用{ask_lang}输出“两阶段摘要”：
1) TL;DR（1~2 句，先总后分，避免口号）
2) **Method Card**：任务/动机、核心方法、关键设计、数据与指标、主要结果与结论、局限与未来工作、保留链接（PDF/代码/项目页）
3) **Discussion Questions**：3~5 个高质量问题（可用于组会讨论）
请保留所有给定链接，不要臆造。scope="{scope}" 表示输出范围（tldr/full/both）。

JSON:
{json.dumps(meta, ensure_ascii=False, indent=2)}
""".strip()
    return user_prompt

def call_llm_two_stage(item: Dict[str, Any], lang: str, scope: str,
                       base_url: str, model: str, api_key: str,
                       system_prompt: str = "") -> Dict[str, str]:
    """
    兼容你原先的“两阶段摘要”接口，内部改为统一 OpenAI 兼容通道。
    """
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": build_llm_prompt(item, lang=lang, scope=scope)})

    text = _chat_completions_request(
        base_url=base_url, api_key=api_key, model=model, messages=messages,
        temperature=0.2, max_tokens=900
    ).strip()

    tldr, full_md = "", ""
    if "TL;DR" in text or "TLDR" in text or "Tl;dr" in text:
        parts = text.splitlines()
        tldr_lines, rest_lines, in_tldr = [], [], False
        for ln in parts:
            if "TL;DR" in ln or "TLDR" in ln or "Tl;dr" in ln:
                in_tldr = True
                t = ln.replace("TL;DR","").replace("TLDR","").replace("Tl;dr","")
                tldr_lines.append(t.strip(" :："))
            elif in_tldr and (ln.strip().startswith("**Method") or ln.strip().lower().startswith("**discussion")):
                in_tldr = False
                rest_lines.append(ln)
            elif in_tldr:
                tldr_lines.append(ln)
            else:
                rest_lines.append(ln)
        tldr = " ".join([s.strip() for s in tldr_lines if s.strip()])
        full_md = "\n".join(rest_lines).strip()
    else:
        full_md = text
    return {"tldr": tldr, "full_md": full_md}

# ========== 标题/摘要中文翻译 ==========

def call_llm_translate(item: Dict[str, Any], target_lang: str,
                       base_url: str, model: str, api_key: str,
                       system_prompt: str = "") -> Dict[str, str]:
    """
    返回：{ title_zh?, summary_zh?, comments_zh? }
    —— 同一 OpenAI 兼容通道，按任意 base_url + api_key 工作。
    """
    title   = item.get("title") or ""
    summary = item.get("summary") or ""
    comments= item.get("comments") or ""
    want_comments = bool(comments.strip())
    schema_keys = ["title_zh", "summary_zh"] + (["comments_zh"] if want_comments else [])

    sys_prompt = system_prompt or (
        "You are a precise academic translator. Translate to Simplified Chinese concisely and faithfully; keep technical terms."
    )
    inst = f"""
Translate the following fields into Simplified Chinese.
Return ONLY compact JSON with keys {schema_keys} (omit keys you can't translate).
Do not add commentary.

DATA:
{json.dumps({"title": title, "summary": summary, "comments": comments}, ensure_ascii=False, indent=2)}
""".strip()

    messages = [{"role":"system","content":sys_prompt},
                {"role":"user","content":inst}]
    text = _chat_completions_request(
        base_url=base_url, api_key=api_key, model=model, messages=messages,
        temperature=0.0, max_tokens=600,
        json_object=True, disable_thinking=True
    ).strip()

    data = _loose_json_load(text)
    required = _require_nonempty_strings(data, ["title_zh", "summary_zh"])
    out: Dict[str, str] = {}
    if isinstance(data, dict):
        out.update(required)
        if "comments_zh" in data and isinstance(data["comments_zh"], str):
            out["comments_zh"] = data["comments_zh"].strip()
    return out
