import unittest
from unittest.mock import patch

import requests

from arxiv_tracker.llm import (_json_loose, _normalize_chat_endpoint, _chat_completions_request,
                               call_llm_bilingual_summary, call_llm_translate, LLMOutputTruncatedError)


class _Response:
    def __init__(self, content, status_code=200):
        self._content = content
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(
                f"HTTP {self.status_code}", response=self
            )

    def json(self):
        return {
            "choices": [
                {"message": {"content": self._content}}
            ]
        }


class _ResponseWithJson(_Response):
    def __init__(self, data):
        self._data = data
        self.status_code = 200

    def json(self):
        return self._data


class LlmRequestTests(unittest.TestCase):
    def truncated(self, content="partial", reasoning_tokens=0):
        return _ResponseWithJson({
            "choices": [{"finish_reason": "length", "message": {"content": content}}],
            "usage": {"completion_tokens": 7000, "completion_tokens_details": {"reasoning_tokens": reasoning_tokens}},
        })

    def chat(self, **options):
        return _chat_completions_request(
            base_url="https://maas.qianwenaiapi.com/compatible-mode/v1",
            api_key="test-only", model="deepseek-v4.1-flash-1017",
            messages=[{"role": "user", "content": "Return JSON"}],
            max_tokens=7000, json_object=True, disable_thinking=True,
            required_json_fields=["digest_en", "digest_zh"], **options,
        )

    @patch("arxiv_tracker.llm.time.sleep")
    @patch("arxiv_tracker.llm.requests.post")
    def test_truncation_retries_with_larger_budget_instead_of_same_request(self, post, sleep):
        post.side_effect = [self.truncated(), self.truncated(), _Response('{"digest_en":"English","digest_zh":"中文"}')]
        with self.assertLogs("arxiv_tracker.llm", level="WARNING") as logs:
            result = self.chat()
        self.assertIn("English", result)
        self.assertEqual([call.kwargs["json"]["max_tokens"] for call in post.call_args_list], [7000, 14000, 28000])
        self.assertIn("max_tokens=14000", "\n".join(logs.output))
        self.assertIn("completion_tokens=7000", "\n".join(logs.output))

    @patch("arxiv_tracker.llm.time.sleep")
    @patch("arxiv_tracker.llm.requests.post")
    def test_truncation_respects_configured_budget_cap(self, post, sleep):
        post.return_value = self.truncated()
        with self.assertRaisesRegex(LLMOutputTruncatedError, "max_tokens=10000"):
            self.chat(max_tokens_limit=10000)
        self.assertEqual([call.kwargs["json"]["max_tokens"] for call in post.call_args_list], [7000, 10000])

    @patch("arxiv_tracker.llm.time.sleep")
    @patch("arxiv_tracker.llm.requests.post")
    def test_truncation_stops_after_three_attempts_even_with_more_budget(self, post, sleep):
        post.return_value = self.truncated()
        with self.assertRaises(LLMOutputTruncatedError):
            self.chat(max_tokens_limit=56000)
        self.assertEqual(post.call_count, 3)

    @patch("arxiv_tracker.llm.time.sleep")
    @patch("arxiv_tracker.llm.requests.post")
    def test_reasoning_only_truncation_is_logged_without_chain_of_thought_or_key(self, post, sleep):
        response = self.truncated("", reasoning_tokens=7000)
        response._data["choices"][0]["message"]["reasoning_content"] = "private reasoning text"
        post.side_effect = [response, _Response('{"digest_en":"English","digest_zh":"中文"}')]
        with self.assertLogs("arxiv_tracker.llm", level="WARNING") as logs:
            self.chat()
        logged = "\n".join(logs.output)
        self.assertIn("reasoning_tokens=7000", logged)
        self.assertIn("content_chars=0", logged)
        self.assertNotIn("private reasoning text", logged)
        self.assertNotIn("test-only", logged)

    @patch("arxiv_tracker.llm.time.sleep")
    @patch("arxiv_tracker.llm.requests.post")
    def test_complete_json_with_length_finish_is_still_retried(self, post, sleep):
        content = '{"digest_en":"English","digest_zh":"中文"}'
        post.side_effect = [self.truncated(content), _Response(content)]
        self.chat()
        self.assertEqual(post.call_count, 2)

    @patch("arxiv_tracker.llm.time.sleep")
    @patch("arxiv_tracker.llm.requests.post")
    def test_malformed_json_retry_keeps_budget_unchanged(self, post, sleep):
        post.side_effect = [_Response("not json"), _Response('{"digest_en":"English","digest_zh":"中文"}')]
        self.chat()
        self.assertEqual([call.kwargs["json"]["max_tokens"] for call in post.call_args_list], [7000, 7000])

    @patch("arxiv_tracker.llm.requests.post")
    def test_invalid_output_cap_fails_before_network_request(self, post):
        with self.assertRaisesRegex(ValueError, "max_tokens_limit"):
            self.chat(max_tokens_limit=1000)
        post.assert_not_called()

    @patch("arxiv_tracker.llm.requests.post")
    def test_deepseek_flash_disables_thinking(self, post):
        post.return_value = _Response('{"digest_en":"English","digest_zh":"中文"}')
        call_llm_bilingual_summary(
            {"title": "Paper", "summary": "Abstract"}, base_url="https://api.deepseek.com",
            model="deepseek-flash", api_key="secret",
        )
        self.assertEqual(post.call_args.kwargs["json"]["thinking"], {"type": "disabled"})

    def test_dashscope_endpoint_normalization(self):
        for base in (
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "https://dashscope.aliyuncs.com/compatible-mode/v1/",
            "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
        ):
            with self.subTest(base=base):
                self.assertEqual(_normalize_chat_endpoint(base),
                                 "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions")

    @patch("arxiv_tracker.llm.requests.post")
    def test_qwen_summary_uses_dashscope_json_and_non_thinking(self, post):
        post.return_value = _Response('{"digest_en":"English","digest_zh":"中文"}')
        result = call_llm_bilingual_summary(
            {"title": "Paper", "summary": "Abstract"},
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            model="qwen-plus", api_key="test-key", system_prompt_en="Medical research",
            system_prompt_zh="半监督医学分割",
        )
        self.assertEqual(result["digest_zh"], "中文")
        self.assertEqual(post.call_args.args[0],
                         "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions")
        payload = post.call_args.kwargs["json"]
        self.assertIs(payload["enable_thinking"], False)
        self.assertNotIn("thinking", payload)
        self.assertEqual(payload["response_format"], {"type": "json_object"})
        self.assertIn("半监督医学分割", payload["messages"][0]["content"])

    @patch("arxiv_tracker.llm.requests.post")
    def test_qwen_translation_uses_non_thinking(self, post):
        post.return_value = _Response('{"title_zh":"论文","summary_zh":"摘要"}')
        call_llm_translate(
            {"title": "Paper", "summary": "Abstract"}, target_lang="zh",
            base_url="https://workspace.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
            model="qwen-plus", api_key="test-key",
        )
        self.assertIs(post.call_args.kwargs["json"]["enable_thinking"], False)

    def test_json_parser_stops_after_first_complete_object(self):
        parsed = _json_loose(
            'prefix {"digest_en":"English","digest_zh":"中文"} '
            'suffix {"unrelated":true}'
        )

        self.assertEqual(
            parsed,
            {"digest_en": "English", "digest_zh": "中文"},
        )

    @patch("arxiv_tracker.llm.requests.post")
    def test_deepseek_v4_bilingual_summary_uses_json_without_thinking(self, post):
        post.return_value = _Response(
            '{"digest_en":"English digest","digest_zh":"中文摘要"}'
        )

        result = call_llm_bilingual_summary(
            {"title": "Paper", "summary": "Abstract"},
            base_url="https://api.deepseek.com",
            model="deepseek-v4-flash",
            api_key="secret",
        )

        self.assertEqual(result["digest_en"], "English digest")
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["response_format"], {"type": "json_object"})
        self.assertEqual(payload["thinking"], {"type": "disabled"})
        self.assertEqual(payload["max_tokens"], 1200)

    @patch("arxiv_tracker.llm.requests.post")
    def test_deepseek_v4_translation_uses_json_without_thinking(self, post):
        post.return_value = _Response(
            '{"title_zh":"论文","summary_zh":"摘要"}'
        )

        result = call_llm_translate(
            {"title": "Paper", "summary": "Abstract"},
            target_lang="zh",
            base_url="https://api.deepseek.com",
            model="deepseek-v4-pro",
            api_key="secret",
        )

        self.assertEqual(result["summary_zh"], "摘要")
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["response_format"], {"type": "json_object"})
        self.assertEqual(payload["thinking"], {"type": "disabled"})
        self.assertEqual(payload["max_tokens"], 1600)

    @patch("time.sleep")
    @patch("arxiv_tracker.llm.requests.post")
    def test_retries_429_until_third_attempt_succeeds(self, post, sleep):
        post.side_effect = [
            _Response("", status_code=429),
            _Response("", status_code=429),
            _Response('{"digest_en":"English","digest_zh":"中文"}'),
        ]

        result = call_llm_bilingual_summary(
            {"title": "Paper", "summary": "Abstract"},
            base_url="https://api.deepseek.com",
            model="deepseek-v4-flash",
            api_key="secret",
        )

        self.assertEqual(result["digest_zh"], "中文")
        self.assertEqual(post.call_count, 3)

    @patch("time.sleep")
    @patch("arxiv_tracker.llm.requests.post")
    def test_retries_5xx_until_third_attempt_succeeds(self, post, sleep):
        post.side_effect = [
            _Response("", status_code=500),
            _Response("", status_code=503),
            _Response('{"digest_en":"English","digest_zh":"中文"}'),
        ]

        result = call_llm_bilingual_summary(
            {"title": "Paper", "summary": "Abstract"},
            base_url="https://api.deepseek.com",
            model="deepseek-v4-flash",
            api_key="secret",
        )

        self.assertEqual(result["digest_en"], "English")
        self.assertEqual(post.call_count, 3)

    @patch("time.sleep")
    @patch("arxiv_tracker.llm.requests.post")
    def test_retries_timeout_until_third_attempt_succeeds(self, post, sleep):
        post.side_effect = [
            requests.Timeout("slow"),
            requests.Timeout("slow"),
            _Response('{"digest_en":"English","digest_zh":"中文"}'),
        ]

        result = call_llm_bilingual_summary(
            {"title": "Paper", "summary": "Abstract"},
            base_url="https://api.deepseek.com",
            model="deepseek-v4-flash",
            api_key="secret",
        )

        self.assertEqual(result["digest_en"], "English")
        self.assertEqual(post.call_count, 3)

    @patch("time.sleep")
    @patch("arxiv_tracker.llm.requests.post")
    def test_stops_after_three_retryable_failures(self, post, sleep):
        post.side_effect = requests.Timeout("slow")

        with self.assertRaises(requests.Timeout):
            call_llm_bilingual_summary(
                {"title": "Paper", "summary": "Abstract"},
                base_url="https://api.deepseek.com",
                model="deepseek-v4-flash",
                api_key="secret",
            )

        self.assertEqual(post.call_count, 3)

    @patch("arxiv_tracker.llm.requests.post")
    def test_does_not_retry_non_retryable_4xx(self, post):
        post.return_value = _Response("", status_code=400)

        with self.assertRaises(requests.HTTPError):
            call_llm_bilingual_summary(
                {"title": "Paper", "summary": "Abstract"},
                base_url="https://api.deepseek.com",
                model="deepseek-v4-flash",
                api_key="secret",
            )

        self.assertEqual(post.call_count, 1)

    @patch("time.sleep")
    @patch("arxiv_tracker.llm.requests.post")
    def test_bilingual_summary_rejects_empty_required_fields(self, post, sleep):
        post.return_value = _Response(
            '{"digest_en":"English digest","digest_zh":"   "}'
        )

        with self.assertRaisesRegex(ValueError, "digest_zh"):
            call_llm_bilingual_summary(
                {"title": "Paper", "summary": "Abstract"},
                base_url="https://api.deepseek.com",
                model="deepseek-v4-flash",
                api_key="secret",
            )

    @patch("time.sleep")
    @patch("arxiv_tracker.llm.requests.post")
    def test_translation_rejects_empty_required_fields(self, post, sleep):
        post.return_value = _Response(
            '{"title_zh":"论文","summary_zh":""}'
        )

        with self.assertRaisesRegex(ValueError, "summary_zh"):
            call_llm_translate(
                {"title": "Paper", "summary": "Abstract"},
                target_lang="zh",
                base_url="https://api.deepseek.com",
                model="deepseek-v4-flash",
                api_key="secret",
            )

    @patch("time.sleep")
    @patch("arxiv_tracker.llm.requests.post")
    def test_translation_retries_invalid_json_before_success(self, post, sleep):
        post.side_effect = [
            _Response("not json"),
            _Response('{"title_zh":"论文","summary_zh":"摘要"}'),
        ]

        result = call_llm_translate(
            {"title": "Paper", "summary": "Abstract"},
            target_lang="zh",
            base_url="https://api.deepseek.com",
            model="deepseek-v4-flash",
            api_key="secret",
        )

        self.assertEqual(result["summary_zh"], "摘要")
        self.assertEqual(post.call_count, 2)
        self.assertEqual(sleep.call_args_list, [unittest.mock.call(1)])

    @patch("time.sleep")
    @patch("arxiv_tracker.llm.requests.post")
    def test_retries_empty_and_invalid_json_before_success(self, post, sleep):
        post.side_effect = [
            _Response(""),
            _Response("not json"),
            _Response('{"digest_en":"English","digest_zh":"中文"}'),
        ]

        result = call_llm_bilingual_summary(
            {"title": "Paper", "summary": "Abstract"},
            base_url="https://api.deepseek.com",
            model="deepseek-v4-flash",
            api_key="secret",
        )

        self.assertEqual(result["digest_en"], "English")
        self.assertEqual(post.call_count, 3)
        self.assertEqual(sleep.call_args_list, [unittest.mock.call(1), unittest.mock.call(2)])

    @patch("time.sleep")
    @patch("arxiv_tracker.llm.requests.post")
    def test_retries_empty_choices_as_empty_content(self, post, sleep):
        post.side_effect = [
            _ResponseWithJson({"choices": []}),
            _Response('{"digest_en":"English","digest_zh":"中文"}'),
        ]

        result = call_llm_bilingual_summary(
            {"title": "Paper", "summary": "Abstract"},
            base_url="https://api.deepseek.com",
            model="deepseek-v4-flash",
            api_key="secret",
        )

        self.assertEqual(result["digest_en"], "English")
        self.assertEqual(post.call_count, 2)
        self.assertEqual(sleep.call_args_list, [unittest.mock.call(1)])

    @patch("time.sleep")
    @patch("arxiv_tracker.llm.requests.post")
    def test_retries_missing_required_fields_before_success(self, post, sleep):
        post.side_effect = [
            _Response('{"digest_en":"English"}'),
            _Response('{"digest_zh":"中文"}'),
            _Response('{"digest_en":"English","digest_zh":"中文"}'),
        ]

        result = call_llm_bilingual_summary(
            {"title": "Paper", "summary": "Abstract"},
            base_url="https://api.deepseek.com",
            model="deepseek-v4-flash",
            api_key="secret",
        )

        self.assertEqual(result["digest_zh"], "中文")
        self.assertEqual(post.call_count, 3)
        self.assertEqual(sleep.call_args_list, [unittest.mock.call(1), unittest.mock.call(2)])

    @patch("time.sleep")
    @patch("arxiv_tracker.llm.requests.post")
    def test_retries_connection_error(self, post, sleep):
        post.side_effect = [
            requests.ConnectionError("connection reset"),
            _Response('{"digest_en":"English","digest_zh":"中文"}'),
        ]

        result = call_llm_bilingual_summary(
            {"title": "Paper", "summary": "Abstract"},
            base_url="https://api.deepseek.com",
            model="deepseek-v4-flash",
            api_key="secret",
        )

        self.assertEqual(result["digest_zh"], "中文")
        self.assertEqual(post.call_count, 2)
        self.assertEqual(sleep.call_args_list, [unittest.mock.call(1)])

    @patch("time.sleep")
    @patch("arxiv_tracker.llm.requests.post")
    def test_exhausted_invalid_json_retries_three_complete_times(self, post, sleep):
        post.side_effect = [_Response("not json")] * 3

        with self.assertRaises(ValueError):
            call_llm_bilingual_summary(
                {"title": "Paper", "summary": "Abstract"},
                base_url="https://api.deepseek.com",
                model="deepseek-v4-flash",
                api_key="secret",
            )

        self.assertEqual(post.call_count, 3)
        self.assertEqual(sleep.call_args_list, [unittest.mock.call(1), unittest.mock.call(2)])


if __name__ == "__main__":
    unittest.main()
