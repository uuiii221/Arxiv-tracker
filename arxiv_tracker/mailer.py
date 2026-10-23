# -*- coding: utf-8 -*-
import os, smtplib, ssl, mimetypes
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email.mime.image import MIMEImage
from email import encoders
from typing import Dict, List, Optional

def _attach_file(msg: MIMEMultipart, filepath: str):
    ctype, encoding = mimetypes.guess_type(filepath)
    if ctype is None or encoding is not None:
        ctype = "application/octet-stream"
    maintype, subtype = ctype.split("/", 1)
    with open(filepath, "rb") as f:
        part = MIMEBase(maintype, subtype)
        part.set_payload(f.read())
    encoders.encode_base64(part)
    part.add_header("Content-Disposition", f'attachment; filename="{os.path.basename(filepath)}"')
    msg.attach(part)

def _send_ssl(smtp_server, smtp_port, smtp_user, smtp_pass, msg, debug, timeout):
    context = ssl.create_default_context()
    with smtplib.SMTP_SSL(smtp_server, smtp_port, context=context, timeout=timeout) as server:
        if debug: server.set_debuglevel(1)
        server.login(smtp_user, smtp_pass)
        server.sendmail(msg["From"], msg["To"].split(", "), msg.as_string())

def _send_starttls(smtp_server, smtp_port, smtp_user, smtp_pass, msg, debug, timeout):
    context = ssl.create_default_context()
    with smtplib.SMTP(smtp_server, smtp_port, timeout=timeout) as server:
        if debug: server.set_debuglevel(1)
        server.ehlo()
        server.starttls(context=context)
        server.ehlo()
        server.login(smtp_user, smtp_pass)
        server.sendmail(msg["From"], msg["To"].split(", "), msg.as_string())

def send_email(
    sender: str,
    to_list: List[str],
    subject: str,
    html_body: str,
    smtp_server: str,
    smtp_port: int,
    smtp_user: str,
    smtp_pass: str,
    tls_mode: str = "auto",              # "ssl" | "starttls" | "auto"
    attachments: Optional[List[str]] = None,
    debug: bool = False,
    timeout: int = 20,
    inline_images: Optional[Dict[str, str]] = None,
):
    msg = MIMEMultipart()
    msg["From"] = sender
    msg["To"] = ", ".join(to_list)
    msg["Subject"] = subject
    related = MIMEMultipart("related")
    related.attach(MIMEText(html_body, "html", "utf-8"))
    total_image_bytes = 0
    for cid, filepath in (inline_images or {}).items():
        with open(filepath, "rb") as image_file:
            data = image_file.read(2 * 1024 * 1024 + 1)
        total_image_bytes += len(data)
        if not data.startswith(b"\x89PNG\r\n\x1a\n") or len(data) > 2 * 1024 * 1024 or total_image_bytes > 8 * 1024 * 1024:
            raise ValueError("Inline figures must be PNGs, <=2 MiB each and <=8 MiB in total")
        image = MIMEImage(data, _subtype="png")
        image.add_header("Content-ID", f"<{cid}>")
        image.add_header("Content-Disposition", "inline", filename=os.path.basename(filepath))
        related.attach(image)
    msg.attach(related)

    for fp in (attachments or []):
        if fp and os.path.exists(fp):
            _attach_file(msg, fp)

    # 发送逻辑
    if tls_mode == "ssl":
        _send_ssl(smtp_server, smtp_port, smtp_user, smtp_pass, msg, debug, timeout)
    elif tls_mode == "starttls":
        _send_starttls(smtp_server, smtp_port, smtp_user, smtp_pass, msg, debug, timeout)
    else:
        # auto: 先 SSL（465），失败则 STARTTLS（587）
        try:
            _send_ssl(smtp_server, smtp_port, smtp_user, smtp_pass, msg, debug, timeout)
        except Exception:
            alt_port = 587 if smtp_port == 465 else smtp_port
            _send_starttls(smtp_server, alt_port, smtp_user, smtp_pass, msg, debug, timeout)
