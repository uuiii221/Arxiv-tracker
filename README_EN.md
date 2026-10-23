# Arxiv-tracker · Semi-supervised Medical Image Segmentation

This fork tracks **semi-supervised medical image segmentation** papers on arXiv, summarizes and translates them with Alibaba Cloud Model Studio Qwen, and produces JSON, Markdown, GitHub Pages and optional email digests.

[中文说明](README.md) · [Process review (Chinese)](PROCESS_REVIEW.md) · [Upstream project](https://github.com/colorfulandcjy0806/Arxiv-tracker)

## Search

The default query requires categories **AND segmentation AND semi-supervision AND a medical context**. `keywords` are ORed; every `keyword_groups` list is another required AND condition, with alternatives inside that list ORed. Hyphen/space variants are expanded automatically.

```yaml
categories: ["cs.CV", "cs.LG", "cs.AI", "eess.IV", "physics.med-ph"]
keywords: ["segmentation"]
keyword_groups:
  - ["semi-supervised", "semisupervised"]
  - ["medical", "biomedical", "organ", "lesion", "tumor", "MRI", "CT"]
logic: "AND"
max_results: 5
sort_by: "lastUpdatedDate"
sort_order: "descending"
```

The full configuration includes additional medical terms. It searches the last 7 days and returns up to 5 unseen papers per run, paginating past previously seen results. Temporarily increase `freshness.since_days` for an initial backfill. This remains lexical retrieval; a paper mentioning the topic only in passing may still match.

## Qwen API

Configure Alibaba Cloud Model Studio, using its [official OpenAI-compatible API](https://www.alibabacloud.com/help/en/model-studio/qwen-api-via-openai-chat-completions):

```yaml
llm:
  provider: "dashscope"
  base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1"
  model: "qwen-plus"
  api_key_env: "DASHSCOPE_API_KEY"
  fail_on_error: true
```

For Beijing you can also use the recommended workspace domain `https://<WorkspaceId>.cn-beijing.maas.aliyuncs.com/compatible-mode/v1`. Replace the placeholder with your workspace ID. Use an endpoint and API key from the same region. The shared Beijing endpoint remains supported.

Direct HTTP requests use Bearer authentication, JSON output, `stream: false` and `enable_thinking: false`. One request produces both summary languages; optional title/abstract translation uses another. Prompts focus on labeled/unlabeled data, pseudo-labels, consistency, datasets, Dice and HD95 without inventing unreported results. Strict failure handling prevents failed summaries or translations from consuming unread papers.

## GitHub deployment

1. Add `DASHSCOPE_API_KEY` as an Actions **Secret**. The default configuration no longer reads `DS_API_KEY`.
2. For email, add Secret `SMTP_PASS` and Variables or Secrets `EMAIL_TO`, `EMAIL_SENDER`, `SMTP_USER`.
3. Enable Pages from `main` / `/docs`, and allow Actions to write repository contents.
4. Run **arxiv-digest** manually. `send_email=false` generates files/site without sending email or advancing seen state; choose `true` for delivery. The scheduled run is daily at **03:00 Asia/Shanghai**, subject to GitHub scheduling delays.

`digest.yml` is the only scheduled workflow. Concurrent runs queue instead of cancelling a run after mail delivery. The former `arxiv_daily.yml` now runs tests on pushes/PRs and performs no retrieval or delivery. The digest commits site, output and state files only after success, and uploads diagnostic artifacts on failure. Scheduled retries may send mail; persistent fingerprints and seen state protect completed deliveries. Empty results send no email.

## Local use

Python 3.10+; from the repository directory:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
$Env:DASHSCOPE_API_KEY = "your Model Studio key"
python test_sf.py
python main.py --no-email --no-state-update --verbose
```

On macOS/Linux use `source .venv/bin/activate` and `export DASHSCOPE_API_KEY="..."` instead. Set email variables if desired, then run `python main.py --verbose` for normal delivery. `--no-email` disables email only; add `--no-state-update` when previewing papers that should remain unread for the next scheduled delivery.

The retained `test_sf.py` is now a guarded Qwen smoke check; importing it never makes network requests or prints key prefixes. PDF export is optional: install `requirements-pdf.txt` and use `--pdf` or enable `email.attach_pdf`.

Optional foreground scheduler, using the machine timezone:

```powershell
python -m arxiv_tracker.scheduler --time 09:00 --config config.yaml
```

Each job invokes the full CLI in a new process. Keep the scheduler running, or use GitHub Actions / your operating system's task scheduler to invoke the CLI once per run.

## State and recovery

The topic has its own `.state/seen_semi_supervised_medical.json`. HTTP/HTTPS, abs/PDF URLs and revisions are normalized to one paper identity; later revisions are not resent. Legacy state formats are supported. Corrupt state causes a visible failure instead of silently resending everything.

State advances atomically after all enabled destinations succeed. Email failures fail the run even when the site was generated. Content fingerprints suppress repeat delivery of the same paper batch to the same recipients. SMTP delivery and Git persistence are not one transaction: a crash after SMTP accepts mail but before durable state can still cause a duplicate. Local runs and Actions have separate state unless synchronized.

Old-topic archives and old state are retained. The first new run updates the homepage; historical pages still contain old topics.

## Tests

```powershell
python -m unittest discover -s tests -v
```

Tests mock network and email. They cover Qwen/DeepSeek request formats and retries, bilingual request reuse, required search groups, pagination, freshness, state migration, malformed/error feeds, email guards, complete local outputs, and failure recovery.
