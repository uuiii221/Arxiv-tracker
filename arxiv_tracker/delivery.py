"""Email delivery with a persistent fingerprint for completed digests."""
import hashlib
import html
import json
import os
from pathlib import Path
import re

import click

from .email_template import render_email_html
from .mailer import send_email
from .tracking import paper_key
from .figures import figure_cid, local_figure_path


def send_digest_email(*, items, email_cfg, out_dir, md_path, pdf_path, page_url, lang,
                      translations, summaries_zh, summaries_en):
    addresses = os.getenv("EMAIL_TO") or email_cfg.get("to") or []
    if isinstance(addresses, str):
        addresses = re.split(r"[;,]", addresses)
    recipients = sorted({address.strip().lower() for address in addresses if address.strip()})
    sender = os.getenv("EMAIL_SENDER") or email_cfg.get("sender") or ""
    password = os.getenv(email_cfg.get("smtp_pass_env") or "SMTP_PASS", "")
    if not (recipients and sender and password):
        raise RuntimeError("Email enabled but EMAIL_TO / EMAIL_SENDER / SMTP_PASS is missing")

    subject = email_cfg.get("subject") or "[arXiv] Digest"
    fingerprint = hashlib.sha256(json.dumps({
        "ids": sorted(paper_key(item["id"]) for item in items),
        "to": recipients, "sender": sender, "subject": subject,
    }, sort_keys=True).encode("utf-8")).hexdigest()
    flag = Path(out_dir) / f"email_sent_{fingerprint}.flag"
    if flag.exists():
        click.echo("[Email] This digest was already delivered; skipped")
        return True

    body = ""
    if page_url:
        url = html.escape(page_url, quote=True)
        body += f'<div>Web 版：<a href="{url}">{url}</a></div>'
    body += render_email_html(
        items=items, lang=lang, translations=translations, summaries_zh=summaries_zh,
        summaries_en=summaries_en, detail=email_cfg.get("detail", "full"),
        max_items=int(email_cfg.get("max_items", 50)), title=subject.replace("[arXiv]", "arXiv"),
    )
    attachments = []
    if email_cfg.get("attach_md") and md_path:
        attachments.append(md_path)
    if email_cfg.get("attach_pdf") and pdf_path:
        attachments.append(pdf_path)
    inline_images = {}
    for item in items[:int(email_cfg.get("max_items", 50))]:
        sid = item.get("id") or ""
        if not ((summaries_zh.get(sid) or {}).get("full_md") or (summaries_en.get(sid) or {}).get("full_md")):
            continue
        for figure in item.get("figures") or []:
            inline_images[figure_cid(figure)] = str(local_figure_path(item, figure))
    send_email(
        sender=sender, to_list=recipients, subject=subject, html_body=body,
        smtp_server=email_cfg.get("smtp_server") or "smtp.qq.com",
        smtp_port=int(email_cfg.get("smtp_port") or 465),
        smtp_user=os.getenv("SMTP_USER") or email_cfg.get("smtp_user") or sender,
        smtp_pass=password, tls_mode=email_cfg.get("tls", "auto"),
        attachments=attachments, inline_images=inline_images, debug=bool(email_cfg.get("debug", False)), timeout=20,
    )
    flag.parent.mkdir(parents=True, exist_ok=True)
    flag.touch()
    click.echo("[Email] Sent")
    return True
