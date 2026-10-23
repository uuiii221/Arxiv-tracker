import unittest
from unittest.mock import MagicMock, patch

from arxiv_tracker.extrascrape import _extract_from_pdf_head


class PdfLinkTests(unittest.TestCase):
    @patch("arxiv_tracker.extrascrape.requests.get")
    def test_range_ignored_response_is_still_bounded_and_closed(self, get):
        response = MagicMock(status_code=200)
        get.return_value.__enter__.return_value = response
        consumed = []

        def chunks():
            for chunk in [b"https://github.com/owner/repo ", b"x" * 100, b"must not download"]:
                consumed.append(chunk)
                yield chunk

        response.iter_content.return_value = chunks()
        links = _extract_from_pdf_head("https://arxiv.org/pdf/2601.00001", timeout=10, head_bytes=64)
        self.assertEqual(links, ["https://github.com/owner/repo"])
        self.assertEqual(len(consumed), 2)
        self.assertTrue(get.call_args.kwargs["stream"])
        get.return_value.__exit__.assert_called_once()

    @patch("arxiv_tracker.extrascrape.requests.get")
    def test_unsuccessful_response_is_optional(self, get):
        get.return_value.__enter__.return_value = MagicMock(status_code=503)
        self.assertEqual(_extract_from_pdf_head("https://arxiv.org/pdf/2601.00001", timeout=10), [])
