from io import BytesIO
import unittest
from unittest.mock import MagicMock, patch

from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject

from arxiv_tracker.fulltext import FullTextError, extract_pdf_text, load_paper_fulltext


def make_pdf(page_count=3, blank=False):
    writer = PdfWriter()
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                             NameObject("/Subtype"): NameObject("/Type1"),
                             NameObject("/BaseFont"): NameObject("/Helvetica")})
    for index in range(1, page_count + 1):
        page = writer.add_blank_page(width=612, height=792)
        if blank:
            continue
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({
            NameObject("/F1"): writer._add_object(font)})})
        lines = [f"PAGE-{index} detailed method and experiments with synthetic evidence " * 2] * 6
        if index == page_count:
            lines.append("LAST-PAGE-EXPERIMENT: baseline 80.0 versus method 87.5")
        stream = DecodedStreamObject()
        commands = "BT /F1 10 Tf 40 740 Td " + " 0 -14 Td ".join(f"({line}) Tj" for line in lines) + " ET"
        stream.set_data(commands.encode("ascii"))
        page[NameObject("/Contents")] = writer._add_object(stream)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


class FullTextTests(unittest.TestCase):
    def test_reads_every_page_including_last_page_results(self):
        text, pages, text_pages = extract_pdf_text(make_pdf())
        self.assertEqual((pages, text_pages), (3, 3))
        self.assertIn("[Page 1]", text)
        self.assertIn("[Page 3]", text)
        self.assertIn("LAST-PAGE-EXPERIMENT", text)

    def test_extracts_from_seekable_disk_file_without_reloading_bytes(self):
        from tempfile import TemporaryFile
        with TemporaryFile(mode="w+b") as source:
            source.write(make_pdf())
            text, pages, text_pages = extract_pdf_text(source)
        self.assertIn("LAST-PAGE-EXPERIMENT", text)
        self.assertEqual((pages, text_pages), (3, 3))

    def test_excessive_length_is_rejected_instead_of_truncated(self):
        with self.assertRaisesRegex(FullTextError, "instead of truncating"):
            extract_pdf_text(make_pdf(), max_chars=100)

    def test_page_limit_is_rejected_instead_of_reading_first_pages(self):
        with self.assertRaisesRegex(FullTextError, "no pages were truncated"):
            extract_pdf_text(make_pdf(), max_pages=2)

    def test_scanned_or_blank_pdf_is_not_an_abstract_fallback(self):
        with self.assertRaisesRegex(FullTextError, "too little extractable text"):
            extract_pdf_text(make_pdf(blank=True))

    def test_html_error_response_is_rejected(self):
        with self.assertRaisesRegex(FullTextError, "not a PDF"):
            extract_pdf_text(b"<html>rate limited</html>")

    @patch("arxiv_tracker.fulltext._wait_for_arxiv")
    @patch("arxiv_tracker.fulltext.requests.get")
    def test_downloads_complete_versioned_pdf_and_records_provenance(self, get, wait):
        pdf = make_pdf()
        response = MagicMock(headers={})
        response.iter_content.return_value = [pdf[:100], pdf[100:]]
        get.return_value.__enter__.return_value = response
        text, info = load_paper_fulltext({"id": "http://arxiv.org/abs/2601.00001v2"})
        self.assertEqual(get.call_args.args[0], "https://arxiv.org/pdf/2601.00001v2")
        self.assertNotIn("Range", get.call_args.kwargs["headers"])
        self.assertTrue(get.call_args.kwargs["stream"])
        self.assertIn("LAST-PAGE-EXPERIMENT", text)
        self.assertEqual(info["pages"], 3)
        self.assertEqual(len(info["sha256"]), 64)
        get.return_value.__exit__.assert_called_once()

    @patch("arxiv_tracker.fulltext._wait_for_arxiv")
    @patch("arxiv_tracker.fulltext.requests.get")
    def test_download_size_limit_works_without_content_length(self, get, wait):
        response = MagicMock(headers={})
        response.iter_content.return_value = [b"%PDF-", b"x" * 100]
        get.return_value.__enter__.return_value = response
        with self.assertRaisesRegex(FullTextError, "download limit"):
            load_paper_fulltext({"id": "2601.00001v1"}, {"max_bytes": 10})

    @patch("arxiv_tracker.fulltext._wait_for_arxiv")
    @patch("arxiv_tracker.fulltext.requests.get")
    def test_default_limit_accepts_reported_46965660_byte_paper(self, get, wait):
        # Reproduce the exact Content-Length that stopped the user's workflow.
        pdf = make_pdf()
        response = MagicMock(headers={"Content-Length": "46965660"})
        response.iter_content.return_value = [pdf]
        get.return_value.__enter__.return_value = response
        text, info = load_paper_fulltext({"id": "2610.31065v1"})
        self.assertIn("LAST-PAGE-EXPERIMENT", text)
        self.assertEqual(info["bytes"], len(pdf))

    @patch("arxiv_tracker.fulltext._wait_for_arxiv")
    @patch("arxiv_tracker.fulltext.requests.get")
    def test_advertised_limit_error_shows_size_and_config_key(self, get, wait):
        response = MagicMock(headers={"Content-Length": "46965660"})
        get.return_value.__enter__.return_value = response
        with self.assertRaisesRegex(FullTextError, r"44\.8 MiB.*fulltext.max_bytes"):
            load_paper_fulltext({"id": "2610.31065v1"}, {"max_bytes": 30 * 1024 * 1024})
        response.iter_content.assert_not_called()

    @patch("arxiv_tracker.fulltext._wait_for_arxiv")
    @patch("arxiv_tracker.fulltext.requests.get")
    @patch("arxiv_tracker.fulltext.extract_pdf_text", side_effect=FullTextError("Cannot parse"))
    @patch("arxiv_tracker.fulltext.TemporaryFile")
    def test_download_temp_file_is_closed_when_parsing_fails(self, temporary, parse, get, wait):
        response = MagicMock(headers={})
        response.iter_content.return_value = [b"%PDF-test"]
        get.return_value.__enter__.return_value = response
        with self.assertRaisesRegex(FullTextError, "Cannot parse"):
            load_paper_fulltext({"id": "2610.31065v1"})
        temporary.return_value.__exit__.assert_called_once()

    @patch("arxiv_tracker.fulltext.requests.get")
    def test_invalid_identifier_does_not_fetch_arbitrary_url(self, get):
        with self.assertRaises(FullTextError):
            load_paper_fulltext({"id": "https://example.com/paper.pdf"})
        get.assert_not_called()
