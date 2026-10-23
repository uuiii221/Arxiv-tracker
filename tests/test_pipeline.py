import os
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import MagicMock, patch

from click.testing import CliRunner
import yaml

from arxiv_tracker.cli import cli
from arxiv_tracker.delivery import send_digest_email
from arxiv_tracker.scheduler import _job
from arxiv_tracker.tracking import load_seen
from arxiv_tracker.email_template import render_email_html
from arxiv_tracker.fulltext import FullTextError
from tests.test_fulltext import make_pdf
from tests.test_reading_notes import report


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / "seen.json"
        self.config = {
            "categories": ["cs.CV"], "keywords": ["segmentation"],
            "keyword_groups": [["semi-supervised"], ["medical"]],
            "max_results": 5, "lang": "both",
            "summary": {"mode": "heuristic"}, "translate": {"enabled": False},
            "email": {"enabled": False},
            "scrape": {"html": False, "pdf_if_missing": False},
            "site": {"enabled": True, "dir": str(self.root / "site"), "title": "医学分割"},
            "freshness": {"unique_only": True, "state_path": str(self.state)},
        }
        self.item = {"id": "https://arxiv.org/abs/2601.00001v1", "title": "Paper",
                     "summary": "Reported Dice improves.", "authors": []}
        self.collect = patch("arxiv_tracker.cli.collect_papers", return_value=[self.item]).start()
        self.addCleanup(patch.stopall)

    def invoke(self, *extra):
        path = self.root / "config.yaml"
        path.write_text(yaml.safe_dump(self.config, allow_unicode=True), encoding="utf-8")
        return CliRunner().invoke(cli, ["run", "--config", str(path), "--out-dir", str(self.root / "outputs"), *extra])

    def test_complete_local_run_writes_site_markdown_and_seen(self):
        result = self.invoke()
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(load_seen(self.state), {"2601.00001"})
        self.assertTrue((self.root / "site" / "index.html").exists())
        markdown = next((self.root / "outputs").glob("*.md")).read_text(encoding="utf-8")
        self.assertIn("Reported Dice improves.", markdown)

    def test_preview_does_not_consume_next_email_batch(self):
        result = self.invoke("--no-email", "--no-state-update")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertFalse(self.state.exists())

    @patch("arxiv_tracker.sitegen.generate_site", side_effect=OSError("disk full"))
    def test_site_failure_keeps_paper_unread_and_returns_failure(self, generate):
        result = self.invoke()
        self.assertNotEqual(result.exit_code, 0)
        self.assertFalse(self.state.exists())

    @patch("arxiv_tracker.delivery.send_digest_email", side_effect=OSError("SMTP unavailable"))
    def test_email_failure_keeps_paper_unread_even_after_site_success(self, send):
        self.config["email"]["enabled"] = True
        result = self.invoke()
        self.assertNotEqual(result.exit_code, 0)
        self.assertTrue((self.root / "site" / "index.html").exists())
        self.assertFalse(self.state.exists())

    @patch.dict(os.environ, {"TEST_MISSING_LLM_KEY": ""})
    def test_missing_qwen_key_fails_before_fetching(self):
        self.config["summary"]["mode"] = "llm"
        self.config["llm"] = {"fail_on_error": True, "api_key_env": "TEST_MISSING_LLM_KEY"}
        result = self.invoke()
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("TEST_MISSING_LLM_KEY", result.output)
        self.collect.assert_not_called()
        self.assertFalse(self.state.exists())

    @patch("arxiv_tracker.cli.build_two_stage_summary", return_value={"_llm_failed": True})
    def test_failed_qwen_summary_is_not_published_or_marked_read(self, summary):
        self.config["summary"]["mode"] = "llm"
        self.config["llm"] = {"fail_on_error": True, "api_key": "test-only"}
        result = self.invoke()
        self.assertNotEqual(result.exit_code, 0)
        self.assertFalse(self.state.exists())
        self.assertFalse((self.root / "site" / "index.html").exists())

    @patch("arxiv_tracker.cli.call_llm_translate", side_effect=ValueError("bad JSON"))
    def test_failed_translation_is_not_marked_read(self, translate):
        self.config["translate"]["enabled"] = True
        self.config["llm"] = {"fail_on_error": True, "api_key": "test-only"}
        result = self.invoke()
        self.assertNotEqual(result.exit_code, 0)
        self.assertFalse(self.state.exists())

    @patch("arxiv_tracker.delivery.send_digest_email")
    def test_empty_result_sends_no_mail(self, send):
        self.collect.return_value = []
        self.config["email"]["enabled"] = True
        result = self.invoke()
        self.assertEqual(result.exit_code, 0, result.output)
        send.assert_not_called()

    @patch("arxiv_tracker.fulltext._wait_for_arxiv")
    @patch("arxiv_tracker.fulltext.requests.get")
    @patch("arxiv_tracker.llm.requests.post")
    def test_five_papers_each_use_full_pdf_and_show_individual_notes_in_all_outputs(self, post, get, wait):
        self.config["summary"].update({"mode": "llm", "source": "fulltext"})
        self.config["scrape"]["pdf_if_missing"] = True  # 全文读取会复用正文，不再另取 PDF 首页
        self.config["llm"] = {"api_key": "test-only", "fail_on_error": True,
                              "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                              "model": "deepseek-v4.1-flash-1017"}
        items = [dict(self.item, id=f"https://arxiv.org/abs/2601.{index:05d}v1", title=f"Paper {index}")
                 for index in range(1, 6)]
        self.collect.return_value = items
        pdf_response = MagicMock(headers={})
        pdf_response.iter_content.return_value = [make_pdf()]
        get.return_value.__enter__.return_value = pdf_response
        llm_responses = []
        for index in range(1, 6):
            data = report()
            data["full_zh"] += f"\n论文{index}的独立解读 [p. 3]"
            response = MagicMock()
            response.json.return_value = {"choices": [{"message": {"content": json.dumps(data)}}]}
            llm_responses.append(response)
        post.side_effect = llm_responses
        result = self.invoke("--no-email")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(get.call_count, 5)
        self.assertEqual(post.call_count, 5)  # 两种语言共用每篇一次请求
        for call in post.call_args_list:
            self.assertIn("LAST-PAGE-EXPERIMENT", call.kwargs["json"]["messages"][1]["content"])
        path = next((self.root / "outputs").glob("*.json"))
        serialized = path.read_text(encoding="utf-8")
        records = json.loads(serialized)
        self.assertEqual(len(records), 5)
        self.assertNotIn("_fulltext", serialized)
        self.assertNotIn("LAST-PAGE-EXPERIMENT", serialized)
        md = next((self.root / "outputs").glob("*.md")).read_text(encoding="utf-8")
        site = (self.root / "site" / "index.html").read_text(encoding="utf-8")
        email = render_email_html(records, summaries_zh={it["id"]: it["notes_zh"] for it in records},
                                  summaries_en={it["id"]: it["notes_en"] for it in records})
        for index in range(1, 6):
            note = f"论文{index}的独立解读"
            self.assertIn(note, md)
            self.assertIn(note, site)
            self.assertIn(note, email)
        self.assertEqual(len(load_seen(self.state)), 5)

    @patch("arxiv_tracker.fulltext.load_paper_fulltext", side_effect=RuntimeError("Full text unavailable"))
    def test_missing_full_text_fails_without_publishing_abstract_fallback(self, load):
        self.config["summary"].update({"mode": "llm", "source": "fulltext"})
        self.config["llm"] = {"api_key": "test-only", "fail_on_error": True}
        result = self.invoke()
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("Full text unavailable", result.output)
        self.assertFalse(self.state.exists())
        self.assertFalse((self.root / "site" / "index.html").exists())

    @patch("arxiv_tracker.fulltext.load_paper_fulltext")
    @patch("arxiv_tracker.summarizer.call_llm_full_paper_summary", return_value=report())
    def test_failed_first_paper_is_replaced_and_remains_unread(self, summary, load):
        self.config["summary"].update({"mode": "llm", "source": "fulltext"})
        self.config["llm"] = {"api_key": "test-only", "fail_on_error": True}
        items = [dict(self.item, id=f"https://arxiv.org/abs/2601.{index:05d}v1") for index in range(1, 7)]
        self.collect.return_value = items
        load.side_effect = [FullTextError("Full PDF exceeds download limit")] + [
            ("[Page 1] Introduction\n[Page 2] Method\n[Page 3] Results",
             {"url": f"https://arxiv.org/pdf/2601.{index:05d}v1", "pages": 3, "text_pages": 3})
            for index in range(2, 7)
        ]
        result = self.invoke()
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(summary.call_count, 5)
        self.assertEqual(load.call_count, 6)
        self.assertEqual(self.collect.call_args.args[1].max_results, 10)
        seen = load_seen(self.state)
        self.assertEqual(len(seen), 5)
        self.assertNotIn("2601.00001", seen)
        self.assertIn("2601.00006", seen)
        failures = json.loads((self.root / "outputs" / "fulltext_failures.json").read_text(encoding="utf-8"))
        self.assertEqual(failures[0]["id"], items[0]["id"])
        records = json.loads(next((self.root / "outputs").glob("arxiv_*.json")).read_text(encoding="utf-8"))
        self.assertEqual(len(records), 5)
        self.assertNotIn(items[0]["id"], [item["id"] for item in records])

    @patch("arxiv_tracker.fulltext.load_paper_fulltext", side_effect=FullTextError("PDF unavailable"))
    def test_all_fulltext_candidates_failing_is_visible_and_preserves_state(self, load):
        self.config["summary"].update({"mode": "llm", "source": "fulltext"})
        self.config["llm"] = {"api_key": "test-only", "fail_on_error": True}
        result = self.invoke()
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("No readable full text", result.output)
        self.assertFalse(self.state.exists())
        self.assertFalse((self.root / "site" / "index.html").exists())
        self.assertTrue((self.root / "outputs" / "fulltext_failures.json").exists())

    @patch("arxiv_tracker.fulltext.load_paper_fulltext")
    @patch("arxiv_tracker.summarizer.call_llm_full_paper_summary", return_value=report())
    def test_partial_batch_delivers_only_readable_papers_and_clears_old_diagnostics(self, summary, load):
        self.config["summary"].update({"mode": "llm", "source": "fulltext"})
        self.config["llm"] = {"api_key": "test-only", "fail_on_error": True}
        failed = dict(self.item, id="2601.00001v1")
        readable = dict(self.item, id="2601.00002v1")
        self.collect.return_value = [failed, readable]
        full = ("[Page 1] Introduction\n[Page 2] Method\n[Page 3] Results",
                {"url": "https://arxiv.org/pdf/2601.00002v1", "pages": 3, "text_pages": 3})
        load.side_effect = [FullTextError("PDF unavailable"), full]
        first = self.invoke()
        self.assertEqual(first.exit_code, 0, first.output)
        self.assertEqual(load_seen(self.state), {"2601.00002"})
        diagnostics = self.root / "outputs" / "fulltext_failures.json"
        self.assertEqual(len(json.loads(diagnostics.read_text(encoding="utf-8"))), 1)
        self.collect.return_value = [dict(self.item, id="2601.00003v1")]
        load.side_effect = None
        load.return_value = full
        second = self.invoke()
        self.assertEqual(second.exit_code, 0, second.output)
        self.assertEqual(json.loads(diagnostics.read_text(encoding="utf-8")), [])

    @patch("arxiv_tracker.fulltext.load_paper_fulltext", side_effect=FullTextError("PDF unavailable"))
    def test_strict_fulltext_mode_can_still_fail_on_first_paper(self, load):
        self.config["summary"].update({"mode": "llm", "source": "fulltext"})
        self.config["llm"] = {"api_key": "test-only", "fail_on_error": True}
        self.config["fulltext"] = {"continue_on_error": False}
        result = self.invoke()
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("PDF unavailable", result.output)
        self.assertEqual(self.collect.call_args.args[1].max_results, 5)
        self.assertFalse(self.state.exists())


class DeliveryTests(unittest.TestCase):
    @patch.dict(os.environ, {"EMAIL_TO": "A@example.com;a@example.com", "EMAIL_SENDER": "sender@example.com", "SMTP_PASS": "test-only"})
    @patch("arxiv_tracker.delivery.send_email")
    def test_persistent_guard_prevents_resend_but_allows_next_digest(self, send):
        with TemporaryDirectory() as folder:
            kwargs = dict(email_cfg={}, out_dir=folder, md_path="", pdf_path="", page_url=None,
                          lang="en", translations={}, summaries_zh={}, summaries_en={})
            item = {"id": "https://arxiv.org/abs/2601.00001v1", "title": "Paper", "authors": []}
            self.assertTrue(send_digest_email(items=[item], **kwargs))
            self.assertTrue(send_digest_email(items=[dict(item, id="http://arxiv.org/abs/2601.00001v2")], **kwargs))
            self.assertEqual(send.call_count, 1)
            self.assertEqual(send.call_args.kwargs["to_list"], ["a@example.com"])
            send_digest_email(items=[dict(item, id="2601.00002")], **kwargs)
            self.assertEqual(send.call_count, 2)

    @patch.dict(os.environ, {"EMAIL_TO": "a@example.com", "EMAIL_SENDER": "sender@example.com", "SMTP_PASS": "test-only"})
    @patch("arxiv_tracker.delivery.send_email", side_effect=OSError("SMTP down"))
    def test_failure_does_not_write_sent_flag(self, send):
        with TemporaryDirectory() as folder:
            with self.assertRaises(OSError):
                send_digest_email(items=[{"id": "2601.00001"}], email_cfg={}, out_dir=folder,
                                  md_path="", pdf_path="", page_url=None, lang="en", translations={},
                                  summaries_zh={}, summaries_en={})
            self.assertEqual(list(Path(folder).glob("*.flag")), [])


class EntryPointTests(unittest.TestCase):
    def test_main_propagates_cli_error(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run([sys.executable, str(root / "main.py"), "--invalid-option"], capture_output=True)
        self.assertEqual(result.returncode, 2)

    @patch("arxiv_tracker.scheduler.subprocess.run")
    def test_scheduler_runs_full_pipeline_and_survives_failure(self, run):
        run.return_value.returncode = 1
        self.assertEqual(_job("config.yaml", "outputs"), 1)
        run.return_value.returncode = 0
        self.assertEqual(_job("config.yaml", "outputs"), 0)
        self.assertIn("arxiv_tracker.cli", run.call_args.args[0])
        self.assertEqual(run.call_count, 2)


if __name__ == "__main__":
    unittest.main()
