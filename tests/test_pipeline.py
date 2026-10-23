import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from click.testing import CliRunner
import yaml

from arxiv_tracker.cli import cli
from arxiv_tracker.delivery import send_digest_email
from arxiv_tracker.scheduler import _job
from arxiv_tracker.tracking import load_seen


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
