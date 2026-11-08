"""Gmail over standard protocols, with an App Password from .env (no Google Cloud project needed):

- send:  SMTP smtp.gmail.com:587 + STARTTLS, plain-text emails only (no HTML, no tracking, no attachments)
- read:  IMAP imap.gmail.com:993, mailbox opened READ-ONLY (EXAMINE), only messages answering our own threads

Follow-ups reuse the first email's subject ("Re: ...") and set In-Reply-To/References so they land in the same thread.
"""
from __future__ import annotations

import email
import imaplib
import os
import re
import smtplib
import ssl
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from email.message import EmailMessage
from email.utils import getaddresses, make_msgid, parsedate_to_datetime
from typing import Iterable, Optional

SMTP_HOST, SMTP_PORT = "smtp.gmail.com", 587
IMAP_HOST, IMAP_PORT = "imap.gmail.com", 993


@dataclass
class GmailConfig:
    address: str = field(default_factory=lambda: os.environ.get("GMAIL_ADDRESS", "").strip())
    app_password: str = field(default_factory=lambda: os.environ.get("GMAIL_APP_PASSWORD", "").replace(" ", "").strip())
    display_name: str = field(default_factory=lambda: os.environ.get("GMAIL_DISPLAY_NAME", "").strip())

    @property
    def configured(self) -> bool:
        return bool(self.address and self.app_password)


def compose(cfg: GmailConfig, to: str, subject: str, body: str, in_reply_to: str = "",
            references: Iterable[str] = ()) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = f"{cfg.display_name} <{cfg.address}>" if cfg.display_name else cfg.address
    msg["To"] = to
    msg["Subject"] = subject
    msg["Message-ID"] = make_msgid(domain=cfg.address.split("@")[-1] or "localhost")
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = " ".join(list(references) or [in_reply_to])
    msg.set_content(body, subtype="plain", charset="utf-8")  # plain text only
    return msg


class Sender:
    """Sends one email per call over a fresh SMTP session. Swap `smtp_factory` in tests."""

    def __init__(self, cfg: GmailConfig, smtp_factory=None):
        self.cfg = cfg
        self.smtp_factory = smtp_factory or (lambda: smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30))

    def send(self, msg: EmailMessage) -> str:
        with self.smtp_factory() as smtp:
            smtp.ehlo()
            smtp.starttls(context=ssl.create_default_context())
            smtp.ehlo()
            smtp.login(self.cfg.address, self.cfg.app_password)
            smtp.send_message(msg)
        return msg["Message-ID"]


@dataclass
class Incoming:
    from_addr: str
    subject: str
    body: str
    message_id: str
    in_reply_to: str
    references: list[str]
    received: Optional[datetime]
    headers: dict


def _text_body(msg: email.message.Message) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and not part.get_filename():
                return part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", "replace")
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                html = part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", "replace")
                return re.sub(r"<[^>]+>", " ", html)
        return ""
    payload = msg.get_payload(decode=True) or b""
    return payload.decode(msg.get_content_charset() or "utf-8", "replace")


def parse(raw: bytes) -> Incoming:
    msg = email.message_from_bytes(raw)
    refs = (msg.get("References") or "").split()
    try:
        received = parsedate_to_datetime(msg.get("Date")) if msg.get("Date") else None
    except (TypeError, ValueError):
        received = None
    return Incoming(from_addr=(getaddresses([msg.get("From", "")]) or [("", "")])[0][1].lower(),
                    subject=str(msg.get("Subject", "")), body=_text_body(msg),
                    message_id=(msg.get("Message-ID") or "").strip(), in_reply_to=(msg.get("In-Reply-To") or "").strip(),
                    references=refs, received=received,
                    headers={k: str(v) for k, v in msg.items() if k.lower() in
                             ("auto-submitted", "x-autoreply", "precedence", "content-type", "x-failed-recipients")})


class Inbox:
    """Read-only look at the inbox. Fetches only recent messages that reference one of our Message-IDs or come from
    an address we've emailed (plus bounce notices). Nothing is marked read, moved or deleted."""

    def __init__(self, cfg: GmailConfig, imap_factory=None):
        self.cfg = cfg
        self.imap_factory = imap_factory or (lambda: imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT))

    def fetch_related(self, our_ids: set[str], addresses: set[str], since: date) -> list[Incoming]:
        found: list[Incoming] = []
        imap = self.imap_factory()
        try:
            imap.login(self.cfg.address, self.cfg.app_password)
            imap.select("INBOX", readonly=True)  # EXAMINE: read-only
            criteria = f'(SINCE "{since:%d-%b-%Y}")'
            typ, data = imap.search(None, criteria)
            ids = (data[0] or b"").split()[-400:]
            for num in ids:
                typ, parts = imap.fetch(num, "(BODY.PEEK[])")  # PEEK: doesn't set the \\Seen flag
                raw = next((p[1] for p in parts if isinstance(p, tuple)), None)
                if not raw:
                    continue
                inc = parse(raw)
                linked = inc.in_reply_to in our_ids or any(r in our_ids for r in inc.references)
                bounce = "mailer-daemon" in inc.from_addr or "postmaster" in inc.from_addr
                if linked or inc.from_addr in addresses or (bounce and any(i.strip("<>") in inc.body for i in our_ids)):
                    found.append(inc)
        finally:
            try:
                imap.logout()
            except Exception:
                pass
        return found


def since_days(days: int) -> date:
    return (datetime.now() - timedelta(days=days)).date()
