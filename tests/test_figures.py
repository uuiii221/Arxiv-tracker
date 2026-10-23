from email import policy
from email.parser import Parser
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch, MagicMock

from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, NameObject

from arxiv_tracker.figures import extract_framework_figure, render_figures_html, local_figure_path
from arxiv_tracker.fulltext import extract_pdf_text, load_paper_fulltext
from arxiv_tracker.mailer import send_email
from tests.test_fulltext import make_pdf


def diagram_pdf(caption="Figure 2: Overview of the proposed training framework and segmentation pipeline.", graphics=True):
    writer = PdfWriter()
    writer.append(PdfReader(BytesIO(make_pdf())))
    page = writer.pages[1]
    content = page.get_contents().get_data()
    diagram = (" q 0.8 0.9 1 rg 60 430 480 180 re B "
               "0 0 0 RG 90 480 100 60 re S 250 480 100 60 re S "
               "190 510 m 250 510 l S Q "
               "BT /F1 12 Tf 100 500 Td (Input) Tj 160 0 Td (Network) Tj ET ") if graphics else ""
    caption_text = f" BT /F1 10 Tf 60 410 Td ({caption}) Tj ET "
    stream = DecodedStreamObject()
    stream.set_data(content + (diagram + caption_text).encode("ascii"))
    page[NameObject("/Contents")] = writer._add_object(stream)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


class FigureTests(unittest.TestCase):
    def extract(self, pdf, folder):
        text, _, _ = extract_pdf_text(pdf)
        return extract_framework_figure(BytesIO(pdf), text, pdf_url="https://arxiv.org/pdf/2601.00001v1",
                                        output_dir=folder)

    def test_renders_vector_diagram_and_caption_with_page_provenance(self):
        with TemporaryDirectory() as folder:
            figure = self.extract(diagram_pdf(), folder)
            self.assertEqual(figure["page"], 2)
            self.assertEqual(figure["number"], 2)
            self.assertEqual(figure["kind"], "figure")
            self.assertTrue(figure["source_url"].endswith("#page=2"))
            self.assertLessEqual(figure["bytes"], 1024 * 1024)
            image = Path(folder, figure["filename"]).read_bytes()
            self.assertTrue(image.startswith(b"\x89PNG"))
            self.assertEqual(sha256(image).hexdigest(), figure["sha256"])

    def test_does_not_substitute_a_result_chart_for_a_method_diagram(self):
        with TemporaryDirectory() as folder:
            figure = self.extract(diagram_pdf("Figure 1: Qualitative segmentation results on test datasets."), folder)
            self.assertIsNone(figure)
            self.assertEqual(list(Path(folder).iterdir()), [])

    def test_uncertain_layout_is_labelled_as_an_entire_page(self):
        with TemporaryDirectory() as folder:
            figure = self.extract(diagram_pdf(graphics=False), folder)
            self.assertEqual(figure["kind"], "page")
            rendered = render_figures_html({"figures": [figure]})
            self.assertIn("完整页面", rendered)
            self.assertIn("cid:", rendered)
            self.assertIn("PDF 第 2 页", rendered)

    @patch("arxiv_tracker.fulltext._wait_for_arxiv")
    @patch("arxiv_tracker.fulltext.requests.get")
    @patch("arxiv_tracker.figures.extract_framework_figure", side_effect=ValueError("figure rendering failed"))
    def test_figure_failure_preserves_valid_full_text(self, extract, get, wait):
        response = MagicMock(headers={})
        response.iter_content.return_value = [make_pdf()]
        get.return_value.__enter__.return_value = response
        item = {"id": "2601.00001v1"}
        with TemporaryDirectory() as folder, self.assertLogs("arxiv_tracker.fulltext", level="WARNING"):
            text, info = load_paper_fulltext(item, figure_config={"enabled": True}, figure_dir=folder)
        self.assertIn("LAST-PAGE-EXPERIMENT", text)
        self.assertEqual(info["pages"], 3)
        self.assertEqual(item["figure_status"], "error")
        self.assertIn("未找到可可靠提取", render_figures_html(item))

    def test_paths_cannot_escape_figure_directory_and_content_is_verified(self):
        with TemporaryDirectory() as folder:
            with self.assertRaises(ValueError):
                local_figure_path({"_figure_dir": folder}, {"filename": "../secret.png"})
            figure = self.extract(diagram_pdf(), folder)
            Path(folder, figure["filename"]).write_bytes(b"modified")
            with self.assertRaisesRegex(ValueError, "checksum"):
                local_figure_path({"_figure_dir": folder}, figure)


class InlineEmailTests(unittest.TestCase):
    @patch("arxiv_tracker.mailer._send_starttls")
    def test_mime_contains_cid_image_in_related_part_and_normal_attachment(self, send):
        with TemporaryDirectory() as folder:
            figure = FigureTests().extract(diagram_pdf(), folder)
            image_path = Path(folder, figure["filename"])
            attachment = Path(folder, "notes.md")
            attachment.write_text("中文论文解读", encoding="utf-8")
            send_email(sender="sender@example.com", to_list=["reader@example.com"], subject="Paper",
                       html_body='<p>方法</p><img src="cid:test@arxiv-tracker">', smtp_server="smtp.qq.com",
                       smtp_port=587, smtp_user="sender", smtp_pass="test-only", tls_mode="starttls",
                       inline_images={"test@arxiv-tracker": str(image_path)}, attachments=[str(attachment)])
        message = Parser(policy=policy.default).parsestr(send.call_args.args[4].as_string())
        self.assertEqual(message.get_content_type(), "multipart/mixed")
        related = next(part for part in message.walk() if part.get_content_type() == "multipart/related")
        image = next(part for part in related.walk() if part.get_content_type() == "image/png")
        self.assertEqual(image["Content-ID"], "<test@arxiv-tracker>")
        self.assertEqual(image.get_content_disposition(), "inline")
        self.assertTrue(image.get_payload(decode=True).startswith(b"\x89PNG"))
        self.assertEqual(len(list(message.iter_attachments())), 1)

    @patch("arxiv_tracker.mailer._send_starttls")
    def test_invalid_inline_image_fails_before_smtp(self, send):
        with TemporaryDirectory() as folder:
            image = Path(folder, "broken.png")
            image.write_bytes(b"not a PNG")
            with self.assertRaises(ValueError):
                send_email(sender="sender@example.com", to_list=["reader@example.com"], subject="Paper",
                           html_body="Paper", smtp_server="smtp.qq.com", smtp_port=587,
                           smtp_user="sender", smtp_pass="test-only", tls_mode="starttls",
                           inline_images={"test@arxiv-tracker": str(image)})
        send.assert_not_called()
