from datetime import datetime, timedelta, timezone
import json
import re
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from arxiv_tracker.config import Settings
from arxiv_tracker.parser import parse_feed
from arxiv_tracker.query import build_search_query
from arxiv_tracker.tracking import collect_papers, load_seen, paper_key, save_seen


def paper(number, age=0, updated_age=None):
    now = datetime.now(timezone.utc)
    return {
        "id": f"https://arxiv.org/abs/2601.{number:05d}v1",
        "title": "Semi-supervised medical image segmentation",
        "published": (now - timedelta(days=age)).isoformat(),
        "updated": (now - timedelta(days=age if updated_age is None else updated_age)).isoformat(),
    }


class QueryTests(unittest.TestCase):
    def matches(self, query, text, category="cs.CV"):
        # Evaluate generated boolean structure against small, synthetic metadata.
        def atom(match):
            field, term = match.group(0).split(":", 1)
            value = category if field == "cat" else text
            return str(term.strip('"').lower() in value.lower())
        expression = re.sub(r'(?:cat|ti|abs|co):(?:"[^"]*"|[^ ()]+)', atom, query)
        expression = expression.replace("ANDNOT", "and not")
        for word in ("AND", "OR", "NOT"):
            expression = re.sub(r"\b" + word + r"\b", word.lower(), expression)
        return eval(expression, {"__builtins__": {}}, {})

    def test_required_groups_are_anded_even_when_categories_use_or(self):
        query = build_search_query(["cs.CV"], ["segmentation"], ["classification"], "OR",
                                   keyword_groups=[["semi-supervised", "semisupervised"], ["medical", "MRI"]])
        for text in ("semi-supervised medical segmentation", "semi supervised MRI segmentation", "semisupervised medical segmentation"):
            self.assertTrue(self.matches(query, text))
        for text in ("fully supervised medical segmentation", "semi-supervised natural image segmentation",
                     "semi-supervised medical classification"):
            self.assertFalse(self.matches(query, text))

    def test_default_config_rejects_medical_classification_and_natural_segmentation(self):
        settings = Settings.from_file(str(Path(__file__).resolve().parents[1] / "config.yaml"))
        query = build_search_query(settings.categories, settings.keywords, settings.exclude_keywords,
                                   settings.logic, keyword_groups=settings.keyword_groups)
        self.assertTrue(self.matches(query, "Semi-supervised cardiac MRI segmentation"))
        self.assertFalse(self.matches(query, "Medical image segmentation with full supervision"))
        self.assertFalse(self.matches(query, "Semi-supervised natural image segmentation"))
        self.assertFalse(self.matches(query, "Semi-supervised medical classification"))

    def test_config_carries_all_three_topic_requirements(self):
        settings = Settings.from_file(str(Path(__file__).resolve().parents[1] / "config.yaml"))
        self.assertEqual(settings.keywords, ["segmentation"])
        self.assertIn("semi-supervised", settings.keyword_groups[0])
        self.assertIn("medical", settings.keyword_groups[1])
        self.assertEqual(settings.logic, "AND")


class TrackingTests(unittest.TestCase):
    def test_identity_ignores_scheme_version_and_pdf_form(self):
        for value in ["http://arxiv.org/abs/2601.00001v2", "https://arxiv.org/pdf/2601.00001v3.pdf", "2601.00001"]:
            self.assertEqual(paper_key(value), "2601.00001")
        self.assertEqual(paper_key("http://arxiv.org/abs/hep-th/9901001v2"), "hep-th/9901001")

    def test_legacy_state_formats_are_migrated(self):
        with TemporaryDirectory() as folder:
            target = Path(folder) / "seen.json"
            identifier = "http://arxiv.org/abs/2601.00001v2"
            for data in [[identifier], {"ids": [identifier]}, {identifier: "timestamp"}]:
                target.write_text(json.dumps(data), encoding="utf-8")
                self.assertEqual(load_seen(target), {"2601.00001"})
            save_seen(target, {"2601.00001", "2601.00002"})
            self.assertEqual(load_seen(target), {"2601.00001", "2601.00002"})
            self.assertFalse(target.with_suffix(".json.tmp").exists())

    def test_corrupt_state_does_not_silently_reset(self):
        with TemporaryDirectory() as folder:
            target = Path(folder) / "seen.json"
            for data in ["invalid JSON", '{"ids":"bad"}', '[123]']:
                target.write_text(data, encoding="utf-8")
                with self.assertRaises(ValueError):
                    load_seen(target)

    @patch("arxiv_tracker.tracking.fetch_arxiv_feed", return_value="feed")
    @patch("arxiv_tracker.tracking.parse_feed")
    def test_pagination_continues_past_seen_first_page(self, parse, fetch):
        parse.side_effect = [[paper(i) for i in range(25)], [paper(25)]]
        seen = {paper_key(paper(i)["id"]) for i in range(25)}
        result = collect_papers("query", Settings(max_results=1), seen, unique_only=True)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["id"], paper(25)["id"])
        self.assertEqual([call.kwargs["start"] for call in fetch.call_args_list], [0, 25])

    @patch("arxiv_tracker.tracking.fetch_arxiv_feed", return_value="feed")
    @patch("arxiv_tracker.tracking.parse_feed")
    def test_deduplicates_versions_inside_one_batch(self, parse, fetch):
        original = paper(1)
        revised = dict(original, id="http://arxiv.org/abs/2601.00001v2")
        parse.return_value = [original, revised, paper(2)]
        result = collect_papers("query", Settings(max_results=5), set())
        self.assertEqual(len(result), 2)

    @patch("arxiv_tracker.tracking.fetch_arxiv_feed", return_value="feed")
    @patch("arxiv_tracker.tracking.parse_feed")
    def test_ascending_skips_old_entries_without_stopping(self, parse, fetch):
        parse.return_value = [paper(1, age=90), paper(2)]
        result = collect_papers("query", Settings(sort_order="ascending"), set(), since_days=7)
        self.assertEqual([item["id"] for item in result], [paper(2)["id"]])

    @patch("arxiv_tracker.tracking.fetch_arxiv_feed", return_value="feed")
    @patch("arxiv_tracker.tracking.parse_feed")
    def test_submitted_date_cutoff_uses_published(self, parse, fetch):
        parse.return_value = [paper(1, age=90, updated_age=0), paper(2)]
        result = collect_papers("query", Settings(sort_by="submittedDate"), set(), since_days=7)
        self.assertEqual(result, [])

    @patch("arxiv_tracker.tracking.fetch_arxiv_feed", return_value="feed")
    @patch("arxiv_tracker.tracking.parse_feed")
    def test_updated_date_cutoff_keeps_recent_revision(self, parse, fetch):
        parse.return_value = [paper(1, age=90, updated_age=0)]
        result = collect_papers("query", Settings(sort_by="lastUpdatedDate"), set(), since_days=7)
        self.assertEqual(len(result), 1)

    @patch("arxiv_tracker.tracking.fetch_arxiv_feed", return_value="feed")
    @patch("arxiv_tracker.tracking.parse_feed")
    def test_page_limit_is_reported(self, parse, fetch):
        parse.return_value = [paper(i) for i in range(25)]
        seen = {paper_key(paper(i)["id"]) for i in range(25)}
        with self.assertRaisesRegex(RuntimeError, "Pagination limit"):
            collect_papers("query", Settings(), seen, unique_only=True, max_pages=1)

    def test_arxiv_error_feed_is_not_treated_as_a_paper(self):
        xml = '''<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
        <entry><id>http://arxiv.org/api/errors#bad_query</id><title>Error</title>
        <summary>bad query</summary></entry></feed>'''
        with self.assertRaisesRegex(ValueError, "arXiv API error"):
            parse_feed(xml)

    def test_html_or_truncated_xml_is_not_treated_as_empty_success(self):
        for value in ["<html><body>Unavailable</body></html>", "<feed><entry>"]:
            with self.assertRaises(ValueError):
                parse_feed(value)
