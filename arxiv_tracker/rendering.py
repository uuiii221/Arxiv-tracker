"""Render generated Markdown without allowing embedded executable HTML."""
import bleach
from markdown import markdown


def render_report(text):
    rendered = markdown(text or "", extensions=["tables", "sane_lists"])
    return bleach.clean(
        rendered,
        tags=["p", "h1", "h2", "h3", "h4", "h5", "h6", "strong", "em", "ul", "ol", "li",
              "blockquote", "pre", "code", "br", "hr", "table", "thead", "tbody", "tr", "th", "td", "a"],
        attributes={"a": ["href", "title"]}, protocols=["https", "http"], strip=True,
    )
