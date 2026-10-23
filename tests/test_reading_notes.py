import json
import unittest
from unittest.mock import MagicMock, patch

from arxiv_tracker.llm import call_llm_full_paper_summary
from arxiv_tracker.rendering import render_report
from arxiv_tracker import summarizer


def report():
    return {"digest_en": "Method improves its reported benchmark.", "digest_zh": "方法改进了文中报告的基准结果。",
            "full_en": "## Method\nUse the stated training objective [p. 2].\n## Results\n87.5 versus 80.0 [p. 3].",
            "full_zh": "## 方法\n使用论文报告的训练目标 [p. 2]。\n## 实验\n87.5 对比 80.0 [p. 3]。"}


def full_item():
    return {"id": "2601.00001v1", "title": "Paper", "summary": "SHORT ABSTRACT ONLY",
            "_fulltext": "[Page 1]\nIntroduction\n[Page 2]\nTRAINING-OBJECTIVE\n[Page 3]\nLAST-PAGE-RESULT 87.5",
            "fulltext_source": {"url": "https://arxiv.org/pdf/2601.00001v1", "pages": 3, "text_pages": 3}}


class ReadingNotesTests(unittest.TestCase):
    @patch("arxiv_tracker.llm.requests.post")
    def test_single_language_requests_and_accepts_only_that_report(self, post):
        for lang, other in (("zh", "en"), ("en", "zh")):
            with self.subTest(lang=lang):
                data = {field: value for field, value in report().items() if field.endswith("_" + lang)}
                response = MagicMock()
                response.json.return_value = {"choices": [{"message": {"content": json.dumps(data)}}]}
                post.return_value = response
                result = call_llm_full_paper_summary(
                    full_item(), base_url="https://maas.qianwenaiapi.com/compatible-mode/v1",
                    model="deepseek-v4.1-flash-1017", api_key="test-only", lang=lang,
                    system_prompt_zh="ZH_SYSTEM", system_prompt_en="EN_SYSTEM",
                )
                self.assertEqual(result, data)
                messages = post.call_args.kwargs["json"]["messages"]
                self.assertEqual(messages[0]["content"], lang.upper() + "_SYSTEM")
                self.assertIn(f"full_{lang}", messages[1]["content"])
                self.assertNotIn(f"full_{other}", messages[1]["content"])
                self.assertIn("LAST-PAGE-RESULT", messages[1]["content"])

    @patch("arxiv_tracker.llm._chat_completions_request")
    def test_chinese_only_report_still_requires_valid_full_paper_evidence(self, chat):
        for reference in (1, 99):
            with self.subTest(reference=reference):
                chat.return_value = json.dumps({"digest_zh": "中文要点", "full_zh": f"方法和结果 [p. {reference}]"})
                with self.assertRaises(ValueError):
                    call_llm_full_paper_summary(
                        full_item(), base_url="https://example.com", model="model", api_key="key", lang="zh",
                    )

    @patch("arxiv_tracker.summarizer.call_llm_full_paper_summary")
    def test_chinese_only_cache_does_not_satisfy_a_later_bilingual_request(self, chat):
        summarizer._BILINGUAL_SUMMARY_CACHE.clear()
        chinese = {field: value for field, value in report().items() if field.endswith("_zh")}
        chat.side_effect = [chinese, report()]
        item, cfg = full_item(), {"api_key": "key"}
        zh = summarizer.build_two_stage_summary(
            item, "llm", "zh", "both", cfg, source="fulltext", output_lang="zh",
        )
        en = summarizer.build_two_stage_summary(
            item, "llm", "en", "both", cfg, source="fulltext", output_lang="both",
        )
        cached = summarizer.build_two_stage_summary(
            item, "llm", "zh", "both", cfg, source="fulltext", output_lang="zh",
        )
        self.assertEqual(zh["digest_en"], "")
        self.assertEqual(zh, cached)
        self.assertIn("## Method", en["full_md"])
        self.assertEqual(chat.call_count, 2)
        self.assertEqual([call.kwargs["lang"] for call in chat.call_args_list], ["zh", "both"])

    @patch("arxiv_tracker.llm.time.sleep")
    @patch("arxiv_tracker.llm.requests.post")
    def test_existing_7000_token_config_recovers_from_truncated_report(self, post, sleep):
        partial = MagicMock()
        partial.json.return_value = {"choices": [{"finish_reason": "length", "message": {"content": '{"full_en":"unfinished'}}]}
        complete = MagicMock()
        complete.json.return_value = {"choices": [{"message": {"content": json.dumps(report())}}]}
        post.side_effect = [partial, complete]
        result = call_llm_full_paper_summary(
            full_item(), base_url="https://maas.qianwenaiapi.com/compatible-mode/v1",
            model="deepseek-v4.1-flash-1017", api_key="test-only", max_tokens=7000,
        )
        self.assertEqual([call.kwargs["json"]["max_tokens"] for call in post.call_args_list], [7000, 14000])
        self.assertIn("87.5", result["full_zh"])

    @patch("arxiv_tracker.llm.requests.post")
    def test_hosted_deepseek_1017_receives_all_pages_and_platform_parameters(self, post):
        response = MagicMock()
        response.json.return_value = {"choices": [{"message": {"content": json.dumps(report())}}]}
        post.return_value = response
        result = call_llm_full_paper_summary(
            full_item(), base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            model="deepseek-v4.1-flash-1017", api_key="test-only",
        )
        payload = post.call_args.kwargs["json"]
        self.assertIs(payload["enable_thinking"], False)
        self.assertNotIn("thinking", payload)
        self.assertIn("LAST-PAGE-RESULT", payload["messages"][1]["content"])
        self.assertIn("TRAINING-OBJECTIVE", payload["messages"][1]["content"])
        self.assertEqual(payload["max_tokens"], 12000)
        self.assertEqual(post.call_args.kwargs["timeout"], 180)
        self.assertIn("87.5", result["full_zh"])

    @patch("arxiv_tracker.llm._chat_completions_request")
    def test_report_requires_pages_beyond_the_abstract_page(self, chat):
        data = report()
        data["full_zh"] = "只复述摘要 [p. 1]"
        chat.return_value = json.dumps(data)
        with self.assertRaisesRegex(ValueError, "only references page 1"):
            call_llm_full_paper_summary(full_item(), base_url="https://example.com", model="model", api_key="key")

    @patch("arxiv_tracker.llm._chat_completions_request")
    def test_invented_page_number_is_rejected(self, chat):
        data = report()
        data["full_en"] = "Results [p. 99]"
        chat.return_value = json.dumps(data)
        with self.assertRaisesRegex(ValueError, "invalid PDF page references"):
            call_llm_full_paper_summary(full_item(), base_url="https://example.com", model="model", api_key="key")

    @patch("arxiv_tracker.summarizer.call_llm_full_paper_summary", side_effect=RuntimeError("failed"))
    def test_fulltext_mode_never_falls_back_to_abstract(self, chat):
        summarizer._BILINGUAL_SUMMARY_CACHE.clear()
        with self.assertRaisesRegex(RuntimeError, "failed"):
            summarizer.build_two_stage_summary(full_item(), "llm", "zh", "both", {"api_key": "key"}, source="fulltext")

    @patch("arxiv_tracker.summarizer.call_llm_full_paper_summary", return_value=report())
    def test_bilingual_notes_share_one_call_and_cache_depends_on_full_text(self, chat):
        summarizer._BILINGUAL_SUMMARY_CACHE.clear()
        item = full_item()
        cfg = {"api_key": "key"}
        zh = summarizer.build_two_stage_summary(item, "llm", "zh", "both", cfg, source="fulltext")
        en = summarizer.build_two_stage_summary(item, "llm", "en", "both", cfg, source="fulltext")
        self.assertIn("## 方法", zh["full_md"])
        self.assertIn("## Method", en["full_md"])
        self.assertEqual(chat.call_count, 1)
        item["_fulltext"] += "\nNEW EXPERIMENT"
        summarizer.build_two_stage_summary(item, "llm", "zh", "both", cfg, source="fulltext")
        self.assertEqual(chat.call_count, 2)

    def test_generated_markdown_is_formatted_without_active_html(self):
        html = render_report('## Results\n87.5 [p. 3]\n<script>alert(1)</script>\n[link](javascript:alert(1))')
        self.assertIn("<h2>Results</h2>", html)
        self.assertNotIn("<script>", html)
        self.assertNotIn('href="javascript:', html)
