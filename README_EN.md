# Arxiv-tracker · Daily arXiv Paper Tracker

Find arXiv papers, read their full text, and produce individual reading notes in JSON, Markdown, website and email outputs.

[中文说明](README.md) · [Process review](PROCESS_REVIEW.md) · [Upstream](https://github.com/colorfulandcjy0806/Arxiv-tracker)

## Search

The default topic is **semi-supervised AND medical image segmentation**. Project branding, email/site titles and assistant prompts remain generic.

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

Keyword alternatives are ORed. Required groups are ANDed with the original query. Hyphen/space variants are expanded. The current rolling window is 365 days, with up to 5 unseen papers per run and pagination past seen results.

## Read full papers

Each selected paper gets its **own** detailed report; the five papers are not combined into a survey. The pipeline downloads the complete versioned arXiv PDF, extracts every page with pypdf, adds page markers, and passes the full extracted text to the model.

One request per paper generates Chinese takeaways and detailed notes by default: problem, method and objective, contribution, experimental setup/baselines/results/ablations, evidence and limitations, and reproduction details. Claims and numbers cite PDF pages. Detailed notes appear in website, email, Markdown and JSON, together with source/page information.

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

`lang: zh` requests only Chinese takeaways and notes and uses them across all outputs. Original English paper titles and abstracts remain source metadata. Explicitly use `--lang en` or `--lang both` for other output languages; bilingual mode still shares one request per paper.

`max_tokens` is the initial output budget for each report. If the model returns `finish_reason: length`, retries double the budget: 12000, 24000, then 48000 by default, with at most three attempts and a `max_tokens_limit` cap. Exhausted retries fail visibly. Logs identify the paper and token budget, plus completion/reasoning token counts when provided. Successful reports normally require one request per paper.

The default download limit is 128 MiB. Complete PDFs stream into temporary disk files, which are cleaned up after parsing. A failed or oversized paper stays unread and is recorded in `outputs/fulltext_failures.json`; later candidates are tried, up to 5 extra, to fill the requested batch. If fewer succeed, only those are delivered; if all fail, the run fails visibly. Set `fulltext.continue_on_error: false` to stop on the first failed paper. Text is never silently truncated or replaced by an abstract while claiming a full-paper report. Invalid page references and reports citing only page 1 are rejected. Increase limits according to the configured model's context capacity.

The standard dependencies include `fonttools` for pypdf to decode certain CFF Type1 font encodings. Reinstall `requirements.txt` after updating.

This is PDF **text** extraction, not OCR or image/table understanding. Unreadable content must be identified rather than guessed. Original PDFs use temporary files and extracted text remains in memory; neither is published; generated notes and provenance are saved. Explicitly set `summary.source: abstract` to use the legacy abstract digest. `scope` accepts `tldr`, `full` or `both`.

## Original method figures

With `figures.enabled: true`, each detailed report can include one original method/framework diagram. The downloaded PDF is reused, caption candidates are matched, and vector graphics and labels are rendered together into PNG. Uncertain crop layouts use a clearly labelled image of the entire source page. Missing figures or extraction errors keep the text report and show a source-PDF notice; Figure 1 and result charts are not used as unconditional substitutes.

Images are embedded as CID attachments in email and copied to the website, including archive pages. Each includes its figure number, PDF page, caption and source link. PNGs persist in `outputs/figures/` and `docs/assets/figures/`; the original PDF remains temporary. Defaults: `figures.width: 1400`, `figures.max_bytes: 1048576` (1 MiB per image). Email rejects inline images exceeding 8 MiB total. Disable with `figures.enabled: false`.

The language model receives text and figure captions, not image pixels. Automatic matching/cropping can depend on layout. Actual QQ inbox rendering has not been tested.

## Model

The API entry point remains the Qwen platform / Model Studio compatible endpoint, while the actual model is **DeepSeek 1017**:

```yaml
llm:
  provider: "dashscope"
  base_url: "https://maas.qianwenaiapi.com/compatible-mode/v1"
  model: "deepseek-v4.1-flash-1017"
  api_key_env: "DASHSCOPE_API_KEY"
  fail_on_error: true
```

Hosted DeepSeek uses `enable_thinking: false`, `stream: false` and JSON output. Direct calls to the official DeepSeek API retain its different `thinking` parameter. Use the base URL matching your existing platform account and region.

References: [Qwen platform Chat API](https://docs.qwencloud.com/api-reference/chat/openai-chat), [Model Studio DeepSeek](https://docs.bailian.console.aliyun.com/en/model-studio/deepseek-api).

## Run

Python 3.10+, from the repository:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
$Env:DASHSCOPE_API_KEY = "your platform key"
python test_sf.py
python main.py --no-email --no-state-update --verbose
```

On macOS/Linux use `source .venv/bin/activate` and `export DASHSCOPE_API_KEY="..."`. Configure email variables and run `python main.py --verbose` for normal delivery. `test_sf.py` checks the model configured in YAML; importing it performs no request.

For GitHub Actions add Secret `DASHSCOPE_API_KEY`, optionally Secret `SMTP_PASS` and Variables/Secrets `EMAIL_TO`, `EMAIL_SENDER`, `SMTP_USER`. Enable Pages from `main` / `/docs` and repository writes for Actions.

The only scheduled workflow, **arxiv-digest**, targets 03:17 Asia/Shanghai, avoiding the busy start of the hour. GitHub can delay or drop scheduled runs under high load; changing the minute reduces risk but cannot guarantee daily execution ([official schedule documentation](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)). Runs queue. Manual `send_email=false` generates files/site without email or seen-state updates. The former `arxiv_daily.yml` is now **tests** on push/PR. Empty results send no email.

In Actions, inspect **arxiv-digest** and distinguish `schedule` from `workflow_dispatch`. Each run's Summary records its trigger, start time in Asia/Shanghai, and manual email option. No run means no recorded trigger; a failed run means execution started and failed. Re-enable a paused workflow using **Enable workflow**. If enabled but today's scheduled run is missing, use **Run workflow**, selecting `send_email=true` for delivery or `false` for preview. `[Email] Sent` confirms the SMTP step completed, not receipt in the inbox.

Optional foreground scheduler, using the machine timezone:

```powershell
python -m arxiv_tracker.scheduler --time 09:00 --config config.yaml
```

Each job invokes the complete CLI. For PDF output export, separately install `requirements-pdf.txt` and use `--pdf`; pypdf text extraction is included in the normal dependencies.

## Recovery and tests

Seen state remains `.state/seen.json`. URL schemes and revisions normalize to one paper identity. All enabled outputs must succeed before atomic state advancement. Persistent email fingerprints protect saved deliveries; SMTP acceptance and Git persistence are not one transaction, so crashes in between can still duplicate email. Local and Actions copies need synchronized state for cross-machine deduplication.

```powershell
python -m unittest discover -s tests -v
```

Tests mock network/SMTP and generate multipage PDFs locally. They verify all-page extraction, last-page evidence in the prompt, five individual reports across every output, hosted DeepSeek parameters, visible failure instead of abstract fallback, page references, limits, pagination/deduplication and scheduling.
