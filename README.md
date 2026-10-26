# Arxiv-tracker · arXiv 每日论文追踪器

检索 arXiv 论文，逐篇读取全文并生成解读，保存 JSON/Markdown、更新网页，并按配置发送邮件。

基于 [原始项目](https://github.com/colorfulandcjy0806/Arxiv-tracker) 修改。[English](README_EN.md) · [运行流程检查](PROCESS_REVIEW.md)

## 检索配置

当前只用两个主题条件：**半监督 AND 医学图像分割**。项目名称、邮件标题、网页标题和阅读助手提示词均保持通用。

```yaml
categories: ["cs.CV", "cs.LG", "cs.AI", "eess.IV"]
keywords: ["medical image segmentation"]
keyword_groups:
  - ["semi-supervised", "semisupervised"]
exclude_keywords: []
logic: "AND"
max_results: 5
sort_by: "lastUpdatedDate"
sort_order: "descending"
```

`keywords` 内部 OR；`keyword_groups` 每组内部 OR，组间及与原查询之间 AND。`semi-supervised` 自动包含 `semi supervised` 写法。当前从最近 365 天找到最多 5 篇未读论文，跳过已读条目后继续分页。首次回溯可增大 `freshness.since_days`。这是关键词检索，仍需阅读论文判断相关性。

当前的 `medical image segmentation` 按完整词组检索。

## 全文解读

默认对本次选出的论文**逐篇独立总结**，不把五篇合为一份综述：

1. 根据 arXiv 的版本 ID 下载完整 PDF。
2. 使用 pypdf 逐页提取全部可读文字，包括方法、实验、消融和结论；给每页加上页码标记。
3. 每篇一次 LLM 请求，默认只生成中文要点和详细解读。
4. 解读包含研究问题、方法步骤与目标函数、贡献、实验设置/基线/结果、证据与局限、复现要点。重要陈述和数字需附 PDF 页码。
5. 邮件、网页、Markdown 和 JSON 都保存每篇的详细解读，并注明 PDF 页数。

```yaml
lang: "zh"
summary:
  mode: "llm"
  source: "fulltext"
  scope: "both"
  max_tokens: 12000
  max_tokens_limit: 48000
  timeout: 180
fulltext:
  timeout: 60
  max_bytes: 134217728
  max_pages: 150
  max_chars: 200000
  continue_on_error: true
  max_extra_candidates: 5
```

`lang: zh` 会让模型只生成中文要点和解读，邮件、网页、Markdown 和 JSON 同步使用中文解读。原论文的英文标题和摘要仍作为原始资料保留。需要其他语言时可显式使用 `--lang en` 或 `--lang both`；双语模式仍共享每篇一次请求。

`max_tokens` 是每篇解读的初始输出额度。模型返回 `finish_reason: length` 时，自动加倍额度重试，默认依次为 12000、24000、48000，最多三次请求；达到 `max_tokens_limit` 或重试耗尽后明确报错。日志记录当前论文、请求额度及平台返回的输出/推理 token 数，便于定位问题。正常生成每篇只需一次请求。

PDF 下载默认上限为 128 MiB，完整文件流式写入临时文件后解析，结束后自动清理。单篇全文获取/解析失败或超限时，记录原因并保留该篇未读，继续尝试后续候选；最多额外尝试 5 篇，尽量补足五篇。候选不足时推送实际成功的篇数，全部失败时明确报错。不会只取首页、静默截断正文，或降级成摘要后标成全文解读。可根据模型上下文能力调整上限。只引用第一页或引用不存在页码的模型解读也会被拒绝。

常规依赖已包含 `fonttools`，用于 pypdf 解析部分 CFF Type1 字体的编码；更新后重新安装 `requirements.txt` 即可。

当前读取的是 PDF 中可提取的文字；扫描件和图片型表格/示意图没有 OCR 或视觉解析，不会声称已经理解这些图像。解析信息不足时会报错，表格或公式无法辨认时要求模型明确说明。原始 PDF 使用本次运行的临时文件，提取的文字保存在内存中，不提交到公开站点或输出文件；保存来源信息和生成的阅读笔记。

如确实只需要原来的摘要简述，可显式设置 `summary.source: abstract`。`scope: tldr/full/both` 控制要点和详细解读的输出。

## 原论文流程图

默认每篇解读附至多一张原论文的方法框架/流程图。下载完整 PDF 后复用同一个临时文件，按图注寻找 framework、pipeline、architecture 等候选，结合页面图形位置裁出原图；矢量图与文字一起渲染为 PNG。位置不明确时展示原图所在完整页面并注明，不会一律拿 Figure 1 或用结果图替代。没有可靠匹配、解析失败时保留文字解读并提示查看原文。

```yaml
figures:
  enabled: true
  width: 1400
  max_bytes: 1048576
```

邮件在每篇详细解读中以 CID 内嵌图片，图片随邮件发送，不依赖外部图床；保留图号、PDF 页码、原始图注和来源链接。网页首页和历史页面也显示原图。PNG 保存于 `outputs/figures/`，网页副本在 `docs/assets/figures/`；原始 PDF 仍在解析后删除。每张默认最多 1 MiB，邮件内嵌图片总量超过 8 MiB 时明确报错。`figures.enabled: false` 可关闭。

图像用于展示，模型仍依据正文和图注解读，不会声称理解图片中不可读的细节。自动图注匹配和裁图可能受 PDF 布局影响，必要时通过来源链接核对原图。未进行真实 QQ 收件客户端的显示测试。

## 模型接入

保留千问平台 / 百炼兼容入口，实际模型使用 **DeepSeek 1017**：

```yaml
llm:
  provider: "dashscope"
  base_url: "https://maas.qianwenaiapi.com/compatible-mode/v1"
  model: "deepseek-v4.1-flash-1017"
  api_key_env: "DASHSCOPE_API_KEY"
  fail_on_error: true
```

平台托管的 DeepSeek 使用 `enable_thinking: false`、`stream: false` 和 JSON 输出。DeepSeek 官方直连则使用其 `thinking` 参数，两种入口分别处理。不会因为平台叫千问就改用 Qwen 模型。

`base_url` 应与现有账号/地域对应；北京工作空间也可用 `https://<WorkspaceId>.cn-beijing.maas.aliyuncs.com/compatible-mode/v1`。如使用其他千问平台域名，保留控制台提供的兼容地址即可。

参考：[千问平台 Chat API](https://docs.qwencloud.com/api-reference/chat/openai-chat)、[百炼 DeepSeek 文档](https://docs.bailian.console.aliyun.com/en/model-studio/deepseek-api)。模型和上下文限制以控制台为准。

## GitHub Actions

1. 在 **Settings → Secrets and variables → Actions** 设置 Secret `DASHSCOPE_API_KEY`。
2. 要发邮件，再设置 Secret `SMTP_PASS`（QQ SMTP 授权码）以及 Variables 或 Secrets `EMAIL_TO`、`EMAIL_SENDER`、`SMTP_USER`。
3. 在 **Settings → Pages** 选择 **Deploy from a branch**，分支 `main`、目录 `/docs`；允许 Actions 写入仓库。
4. 手动运行 **arxiv-digest**。默认 `send_email=false`：生成网页/文件，不发邮件、不推进未读状态。正式推送选 `true`。

每日任务计划在北京时间 **03:17** 运行，避开整点高峰。只保留 `digest.yml` 一个每日任务；同组任务排队，成功后提交网页、输出和状态。原来的 `arxiv_daily.yml` 现在是无外部请求的 **tests** 工作流。没有新论文时不发空邮件。

GitHub 定时调度可能延迟或在高负载时丢弃，修改分钟数只能降低风险，不能保证每天准点运行（[官方说明](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)）。Actions 中查看 **arxiv-digest**，通过 `schedule` / `workflow_dispatch` 区分自动和手动运行；运行 Summary 会记录触发来源、北京时间和手动发信选项。没有运行记录表示尚未触发；红色失败表示已触发但执行失败，两者处理方式不同。

暂停后恢复：进入 Actions → arxiv-digest → **Enable workflow**。若已启用但当天缺少自动记录，可手动 **Run workflow**；需要正式推送时选 `send_email=true`，预览选 `false`。成功日志中的 `[Email] Sent` 表示 SMTP 发信步骤完成，收件情况仍以邮箱为准。

## 本地运行

Python 3.10+，在项目目录执行：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
$Env:DASHSCOPE_API_KEY = "你的平台 API Key"
python test_sf.py
python main.py --no-email --no-state-update --verbose
```

`test_sf.py` 读取配置里的模型进行手动连接检查，导入时不联网，不打印密钥。

正式运行前按需设置邮箱变量，然后执行 `python main.py --verbose`。macOS/Linux 使用 `source .venv/bin/activate` 和 `export DASHSCOPE_API_KEY="..."`。

纯预览同时传 `--no-email --no-state-update`。`--no-email` 单独使用仍会将成功的文件/网页输出记为已读。

可选的 Markdown 转 PDF 导出另装 `python -m pip install -r requirements-pdf.txt`，再传 `--pdf`；这与用于读取论文的 pypdf 是不同用途。

本地常驻定时器：

```powershell
python -m arxiv_tracker.scheduler --time 09:00 --config config.yaml
```

它按机器时区每天启动一次完整 CLI，窗口必须保持运行。通常用 GitHub Actions 或操作系统任务计划程序直接运行一次 CLI 就足够。

## 状态与重试

已读记录使用 `.state/seen.json`。HTTP/HTTPS、Abs/PDF URL 和不同版本统一为同一论文，后续版本不重复推送。所有启用的输出成功后才原子保存成功论文的状态。被跳过的全文始终保留未读，原因写入 `outputs/fulltext_failures.json`；全部全文失败或 LLM、邮件、网页失败会返回非零退出码。若希望单篇全文失败就停止，可设 `fulltext.continue_on_error: false`。

发送标记用论文批次、收件人、发件人和主题的稳定指纹保护已保存的发送记录。SMTP 与 Git 提交之间仍不是事务，发送后、标记持久化前崩溃可能重复；本地和 Actions 也需要共享/同步状态才能跨机器去重。

旧网页和历史归档保留，后续运行使用通用站点标题。

## 测试

```powershell
python -m unittest discover -s tests -v
```

测试使用模拟网络/SMTP 和本地生成的多页 PDF。覆盖每页读取、末页实验进入模型输入、五篇分别生成解读并显示于所有输出、平台 DeepSeek 参数、失败不降级/不记已读、长度限制、页面引用、分页去重和调度流程。
