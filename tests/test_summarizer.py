import unittest
from unittest.mock import patch

from arxiv_tracker import summarizer
from arxiv_tracker.summarizer import build_two_stage_summary


class SummarizerTests(unittest.TestCase):
    def setUp(self):
        summarizer._BILINGUAL_SUMMARY_CACHE.clear()

    @patch("arxiv_tracker.summarizer.call_llm_bilingual_summary")
    def test_both_languages_share_one_bilingual_summary_request(self, call_summary):
        call_summary.return_value = {
            "digest_en": "English digest",
            "digest_zh": "中文摘要",
        }
        item = {
            "id": "2401.00001",
            "title": "Paper",
            "summary": "Abstract",
        }
        cfg = {
            "api_key": "secret",
            "base_url": "https://api.deepseek.com",
            "model": "deepseek-v4-flash",
        }

        zh = build_two_stage_summary(item, "llm", "zh", "both", cfg)
        en = build_two_stage_summary(item, "llm", "en", "both", cfg)

        self.assertEqual(zh["digest_zh"], "中文摘要")
        self.assertEqual(en["digest_en"], "English digest")
        self.assertEqual(call_summary.call_count, 1)

    @patch("arxiv_tracker.summarizer.call_llm_bilingual_summary")
    def test_summary_failure_is_logged_before_fallback(self, call_summary):
        call_summary.side_effect = ValueError("missing digest_zh")
        item = {
            "id": "2401.00002",
            "title": "Paper",
            "summary": "First sentence. Second sentence.",
        }
        cfg = {
            "api_key": "secret",
            "base_url": "https://api.deepseek.com",
            "model": "deepseek-v4-flash",
        }

        with self.assertLogs("arxiv_tracker.summarizer", level="ERROR") as logs:
            result = build_two_stage_summary(item, "llm", "en", "both", cfg)

        self.assertEqual(result["digest_en"], "First sentence.")
        self.assertIn("2401.00002", "\n".join(logs.output))
        self.assertIn("missing digest_zh", "\n".join(logs.output))

    @patch("arxiv_tracker.summarizer.call_llm_bilingual_summary")
    def test_both_languages_share_one_failed_summary_request(self, call_summary):
        call_summary.side_effect = ValueError("missing digest_zh")
        item = {
            "id": "2401.00003",
            "title": "Paper",
            "summary": "First sentence. Second sentence.",
        }
        cfg = {
            "api_key": "secret",
            "base_url": "https://api.deepseek.com",
            "model": "deepseek-v4-flash",
        }

        with self.assertLogs("arxiv_tracker.summarizer", level="ERROR"):
            zh = build_two_stage_summary(item, "llm", "zh", "both", cfg)
            en = build_two_stage_summary(item, "llm", "en", "both", cfg)

        self.assertEqual(zh, en)
        self.assertEqual(call_summary.call_count, 1)


if __name__ == "__main__":
    unittest.main()
