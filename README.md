# Arxiv-tracker · 半监督医学图像分割论文追踪

本 fork 面向 **半监督医学图像分割**，每天从 arXiv 检索论文，用阿里云百炼千问生成双语总结和中文翻译，再保存 Markdown/JSON、更新 GitHub Pages，并按配置发送邮件。

基于 [原始项目](https://github.com/colorfulandcjy0806/Arxiv-tracker) 修改。[English](README_EN.md) · [流程检查记录](PROCESS_REVIEW.md)

## 检索方向

检索逻辑为：

```text
(cs.CV OR cs.LG OR cs.AI OR eess.IV OR physics.med-ph)
AND segmentation
AND (semi-supervised OR semi supervised OR semisupervised)
AND (medical OR biomedical OR organ OR lesion OR tumor OR ...)
```

`keywords` 内部按 OR 合并；新增的 `keyword_groups` 每组内部按 OR、组与组之间按 AND，并始终约束原查询。这样不会因为把所有词放进同一个 OR 列表而混入大量全监督医学分割或自然图像半监督论文。连字符和空格写法会自动展开，检索覆盖标题、摘要和 comments。

```yaml
categories: ["cs.CV", "cs.LG", "cs.AI", "eess.IV", "physics.med-ph"]
keywords: ["segmentation"]
keyword_groups:
  - ["semi-supervised", "semisupervised"]
  - ["medical", "biomedical", "organ", "lesion", "tumor", "MRI", "CT"]
exclude_keywords: []
logic: "AND"
max_results: 5
sort_by: "lastUpdatedDate"
sort_order: "descending"
```

仓库的 `config.yaml` 包含更完整的医学术语。默认在最近 7 天中寻找最多 5 篇未读论文；首次回溯可暂时增大 `freshness.since_days`。它是每次输出条数，不是检索总量：已读条目会跳过并继续分页。

这是关键词检索，仍可能命中仅在摘要中提及目标方向的论文；请结合摘要判断相关性。`self-supervised`、`few-shot` 或 `weakly-supervised` 单独出现不会满足半监督条件，也不全局排除这些词，避免误删同时讨论它们的目标论文。

## 千问 API

使用 [阿里云百炼 OpenAI 兼容 Chat Completions 接口](https://www.alibabacloud.com/help/en/model-studio/qwen-api-via-openai-chat-completions)：

```yaml
llm:
  provider: "dashscope"
  base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1"
  model: "qwen-plus"
  api_key_env: "DASHSCOPE_API_KEY"
  fail_on_error: true
```

北京地域也可使用官方推荐的工作空间域名 `https://<WorkspaceId>.cn-beijing.maas.aliyuncs.com/compatible-mode/v1`，把 `<WorkspaceId>` 替换为真实工作空间 ID。其他地域请使用控制台对应的域名和 API Key；不要把千问聊天网页 URL 当成 API 地址。通用北京域名目前仍受支持。

请求通过 `requests` 直接发送 Bearer Key 和 JSON；千问摘要与翻译使用 `enable_thinking: false`、`stream: false` 和 JSON 输出格式。每篇论文一次请求同时生成中英文总结，启用翻译时再请求一次。摘要关注标注比例、伪标签、师生模型、一致性约束、数据集及 Dice/HD95，仅保留原摘要实际报告的信息。

默认 `fail_on_error: true`：缺少密钥、摘要或翻译失败会让任务失败，且不推进已读状态。旧的其他 OpenAI 兼容接口仍可通过修改 `base_url`、`model`、`api_key_env` 使用；DeepSeek 原有参数兼容测试保留。

## GitHub Actions 部署

1. 在仓库 **Settings → Secrets and variables → Actions** 添加 Secret `DASHSCOPE_API_KEY`，填写百炼 API Key。旧的 `DS_API_KEY` 不再被默认配置读取。
2. 要发送邮件，再设置 Secret `SMTP_PASS`（QQ SMTP 授权码），以及 Variables 或 Secrets `EMAIL_TO`、`EMAIL_SENDER`、`SMTP_USER`。多收件人用逗号或分号分隔。
3. 在 **Settings → Pages** 选择 **Deploy from a branch**，分支 `main`、目录 `/docs`；允许 Actions 写入仓库。每日工作流只会从默认分支的已合并代码触发。
4. 运行 **arxiv-digest**。手动 `send_email=false` 默认只生成网页/文件，不发邮件、不推进已读状态；正式试发选择 `true`。默认每日北京时间 **03:00** 运行，GitHub 可能延迟调度。

`.github/workflows/digest.yml` 是唯一每日任务，同组任务排队执行，成功后提交 `docs/**`、`outputs/**`、`.state/**`。原来的 `arxiv_daily.yml` 已改为无外部请求的测试工作流，界面名称为 **tests**。失败运行会保留输出 artifact，便于排查。

定时任务失败后可重跑；定时重跑仍允许发信，已持久化的去重状态和邮件标记会避免重复。没有新增论文时不发空邮件。

## 本地运行

Python 3.10+。在项目目录执行：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
$Env:DASHSCOPE_API_KEY = "你的百炼 API Key"
python test_sf.py
python main.py --no-email --no-state-update --verbose
```

`test_sf.py` 现在是手动千问连接检查，只有直接执行才会请求 API，不打印密钥。正式运行：

```powershell
$Env:EMAIL_TO = "your@qq.com"
$Env:EMAIL_SENDER = "your@qq.com"
$Env:SMTP_USER = "your@qq.com"
$Env:SMTP_PASS = "QQ SMTP 授权码"
python -m arxiv_tracker.cli run --config config.yaml --verbose
```

macOS/Linux 使用 `source .venv/bin/activate` 和 `export DASHSCOPE_API_KEY="..."`，其余命令相同。`--no-email` 只禁用邮件；要预览并留给下一次正式推送，配合 `--no-state-update`。

可选 PDF 导出另装 `python -m pip install -r requirements-pdf.txt`，再传 `--pdf`；`email.attach_pdf: true` 也会生成附件。普通网页/邮件运行无需安装 Cairo/PDF 依赖。

如果使用本地常驻定时器：

```powershell
python -m arxiv_tracker.scheduler --time 09:00 --config config.yaml
```

它按机器本地时区触发，每次启动完整 CLI 子进程，前一次完成后再检查下次任务；窗口必须保持运行。通常选 GitHub Actions 或操作系统定时任务运行一次 CLI 即可。

## 去重与故障恢复

- 已读状态独立保存于 `.state/seen_semi_supervised_medical.json`。HTTP/HTTPS、PDF/Abs URL 和 v1/v2 统一为同一论文；同一论文更新版本不会再次推送。
- 所有启用的输出成功后才原子写入状态。SMTP 或站点失败会返回非零退出码；`main.py` 会向调用者传递退出码。
- 邮件使用论文 ID、收件人、发件人、主题的稳定指纹保存发送标记。正常跨日运行可继续发新论文；同批次重试会检查已保存标记。
- SMTP 接受邮件与 Git 提交之间无法保证事务性“严格只发一次”；若发送后进程崩溃且标记未持久化，仍可能重复。详见流程检查记录。
- 旧方向的 `docs/archive/` 和 `.state/seen.json` 保留；首次新方向运行后首页变为新主题，历史页面仍包含旧内容。

## 测试

```powershell
python -m unittest discover -s tests -v
```

测试覆盖千问/DeepSeek 请求格式、重试、双语请求复用、检索组合、分页与时间窗、旧状态迁移、异常 Feed、邮件去重、完整本地输出，以及失败时不推进状态。测试使用模拟响应，不需要 API Key，不发送邮件。
