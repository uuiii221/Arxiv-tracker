# -*- coding: utf-8 -*-
import os
from typing import Optional
from markdown import markdown

_BASE_CSS = """
body{font-family:DejaVu Sans, Arial, Helvetica, sans-serif;font-size:12pt;line-height:1.5;color:#111;}
h1,h2,h3{color:#111;margin:8pt 0 6pt 0;}
code,pre{font-family:DejaVu Sans Mono, Menlo, Consolas, monospace;}
pre{background:#f6f8fa;padding:8pt;border-radius:6pt;white-space:pre-wrap;}
ul,ol{margin:6pt 0 6pt 18pt;}
a{color:#0366d6;text-decoration:none;}
hr{border:0;border-top:1pt solid #ddd;margin:10pt 0;}
blockquote{border-left:3pt solid #ddd;padding:6pt 10pt;background:#fafafa;border-radius:6pt;}
"""

def md_to_pdf(md_path: str, pdf_path: Optional[str] = None) -> str:
    """
    将 Markdown 文件转换为 PDF（基于 markdown -> HTML -> xhtml2pdf）
    返回生成的 pdf 路径
    """
    from xhtml2pdf import pisa

    if not os.path.exists(md_path):
        raise FileNotFoundError(md_path)
    if not pdf_path:
        base, _ = os.path.splitext(md_path)
        pdf_path = base + ".pdf"
    with open(md_path, "r", encoding="utf-8") as f:
        md = f.read()

    html_body = markdown(md, extensions=["extra", "toc", "tables", "fenced_code"])
    html_doc = f"""<html><head><meta charset="utf-8"><style>{_BASE_CSS}</style></head>
    <body>{html_body}</body></html>"""

    with open(pdf_path, "wb") as out:
        pisa.CreatePDF(src=html_doc, dest=out)  # 返回值可忽略；失败会抛异常
    return pdf_path
