import unittest
from unittest.mock import patch

import requests

from arxiv_tracker.llm import _json_loose, call_llm_bilingual_summary, call_llm_translate


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
