# Arxiv-tracker · Daily arXiv Paper Tracker

Find arXiv papers, read their full text, and produce individual reading notes in JSON, Markdown, website and email outputs.

[中文说明](README.md) · [Process review](PROCESS_REVIEW.md) · [Upstream](https://github.com/colorfulandcjy0806/Arxiv-tracker)

## Search

The default topic is simply **semi-supervised AND segmentation**. There are no additional medical, boundary, dataset or method keywords. Project branding, email/site titles and assistant prompts remain generic.

```yaml
categories: ["cs.CV", "cs.LG", "cs.AI", "eess.IV"]
keywords: ["segmentation"]
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

One request per paper generates bilingual takeaways and detailed notes: problem, method and objective, contribution, experimental setup/baselines/results/ablations, evidence and limitations, and reproduction details. Claims and numbers cite PDF pages. Detailed notes appear in website, email, Markdown and JSON, together with source/page information.

```yaml
summary:
  mode: "llm"
  source: "fulltext"
  scope: "both"
  max_tokens: 7000
  timeout: 120
fulltext:
  timeout: 60
  max_bytes: 134217728
  max_pages: 150
  max_chars: 200000
  continue_on_error: true
  max_extra_candidates: 5
```

The default download limit is 128 MiB. Complete PDFs stream into temporary disk files, which are cleaned up after parsing. A failed or oversized paper stays unread and is recorded in `outputs/fulltext_failures.json`; later candidates are tried, up to 5 extra, to fill the requested batch. If fewer succeed, only those are delivered; if all fail, the run fails visibly. Set `fulltext.continue_on_error: false` to stop on the first failed paper. Text is never silently truncated or replaced by an abstract while claiming a full-paper report. Invalid page references and reports citing only page 1 are rejected. Increase limits according to the configured model's context capacity.

This is PDF **text** extraction, not OCR or image/table understanding. Unreadable content must be identified rather than guessed. Original PDFs use temporary files and extracted text remains in memory; neither is published; generated notes and provenance are saved. Explicitly set `summary.source: abstract` to use the legacy abstract digest. `scope` accepts `tldr`, `full` or `both`.

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

The only scheduled workflow, **arxiv-digest**, runs at 03:00 Asia/Shanghai, subject to GitHub delays. Runs queue. Manual `send_email=false` generates files/site without email or seen-state updates. The former `arxiv_daily.yml` is now **tests** on push/PR. Empty results send no email.

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
