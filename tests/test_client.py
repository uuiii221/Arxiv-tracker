import unittest
from unittest.mock import MagicMock, patch

import requests

from arxiv_tracker import client


class ArxivClientTests(unittest.TestCase):
    @patch("arxiv_tracker.client.time.sleep")
    @patch("arxiv_tracker.client.time.monotonic", side_effect=[11.0, 13.0])
    def test_requests_leave_at_least_three_seconds_between_starts(self, monotonic, sleep):
        with patch.object(client, "_last_request_at", 10.0):
            client._wait_for_arxiv()
            sleep.assert_called_once_with(2.0)
            self.assertEqual(client._last_request_at, 13.0)

    @patch("arxiv_tracker.client._do_get")
    def test_exhausted_rate_limit_does_not_switch_protocol_and_repeat(self, get):
        response = MagicMock(status_code=429)
        get.side_effect = requests.HTTPError("HTTP 429", response=response)
        with self.assertRaises(requests.HTTPError):
            client.fetch_arxiv_feed("query")
        self.assertEqual(get.call_count, 1)

    @patch("arxiv_tracker.client._do_get")
    def test_connection_error_can_still_use_legacy_fallback(self, get):
        response = MagicMock(text="feed")
        get.side_effect = [requests.ConnectionError("connection failed"), response]
        self.assertEqual(client.fetch_arxiv_feed("query"), "feed")
        self.assertEqual(get.call_count, 2)
