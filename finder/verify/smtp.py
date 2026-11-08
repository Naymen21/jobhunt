"""Step 4 + 5: SMTP mailbox probe and catch-all detection.

Opens a session to the domain's MX, runs EHLO/HELO, MAIL FROM, RCPT TO for the
target (and optionally a random gibberish address), then QUIT.
DATA is never sent: no email is ever delivered.
"""
from __future__ import annotations

import secrets
import smtplib
import socket
import string
from dataclasses import dataclass, field
from typing import Optional

from ..config import Settings
from ..deadline import Deadline, DeadlineExceeded
from ..models import SmtpResponse

TRANSPORTS = ("disabled", "direct", "socks")

# 5xx replies that mean "we don't like *you*", not "that mailbox doesn't exist".
_POLICY_HINTS = (
    "5.7.", "spamhaus", "blocklist", "block list", "blacklist", "blocked", "policy",
    "reputation", "not permitted", "access denied", "client host", "rbl", "dnsbl",
    "relay", "authentication required", "pbl", "residential", "dynamic",
)


def is_policy_rejection(code: int, message: str) -> bool:
    msg = message.lower()
    return 500 <= code < 600 and any(h in msg for h in _POLICY_HINTS)


@dataclass
class ProbeResult:
    skipped: bool = False
    error: Optional[str] = None  # connection-level failure
    host: Optional[str] = None
    responses: list[SmtpResponse] = field(default_factory=list)
    rcpt: Optional[SmtpResponse] = None
    random_rcpt: Optional[SmtpResponse] = None
    random_address: Optional[str] = None
    detail: str = ""


def random_local_part() -> str:
    alphabet = string.ascii_lowercase + string.digits
    return "zz" + "".join(secrets.choice(alphabet) for _ in range(14))


class _DeadlineSMTP(smtplib.SMTP):
    """smtplib.SMTP whose connect honours a shared Deadline.

    socket.create_connection gives *each* resolved address (IPv4 and IPv6) the full
    timeout, so one host could eat 2x the timeout. Here every attempt is capped by
    what's left of the overall budget.
    """

    def __init__(self, deadline: Deadline, **kwargs):
        self._deadline = deadline
        super().__init__(**kwargs)

    def _get_socket(self, host, port, timeout):
        last_err: Optional[BaseException] = None
        for family, socktype, proto, _, addr in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM):
            sock = socket.socket(family, socktype, proto)
            try:
                sock.settimeout(self._deadline.cap(timeout))
                sock.connect(addr)
                return sock
            except DeadlineExceeded:
                sock.close()
                raise
            except OSError as e:
                sock.close()
                last_err = e
        raise last_err or OSError(f"no addresses for {host}")


class _SocksSMTP(_DeadlineSMTP):
    """smtplib.SMTP that tunnels its TCP connection through a SOCKS5 proxy."""

    def __init__(self, proxy: str, deadline: Deadline, **kwargs):
        host, _, port = proxy.rpartition(":")
        self._proxy = (host or "127.0.0.1", int(port or 1080))
        super().__init__(deadline, **kwargs)

    def _get_socket(self, host, port, timeout):
        import socks  # PySocks

        sock = socks.socksocket()
        sock.set_proxy(socks.SOCKS5, self._proxy[0], self._proxy[1], rdns=True)
        sock.settimeout(self._deadline.cap(timeout))
        sock.connect((host, port))
        return sock


def _decode(msg) -> str:
    if isinstance(msg, bytes):
        msg = msg.decode("utf-8", errors="replace")
    return " ".join(str(msg).split())[:300]


def _client(settings: Settings, deadline: Deadline) -> _DeadlineSMTP:
    kwargs = dict(local_hostname=settings.helo_host, timeout=settings.smtp_timeout)
    if settings.smtp_transport == "socks":
        return _SocksSMTP(settings.socks_proxy, deadline, **kwargs)
    return _DeadlineSMTP(deadline, **kwargs)


def _arm(smtp: smtplib.SMTP, settings: Settings, deadline: Deadline) -> None:
    """Shrink the socket timeout before each command so the whole session fits the budget."""
    if smtp.sock is not None:
        smtp.sock.settimeout(deadline.cap(settings.smtp_timeout))


def probe(mx_hosts: list[str], email: str, settings: Settings, test_catch_all: bool = True,
          deadline: Optional[Deadline] = None) -> ProbeResult:
    deadline = deadline or Deadline(settings.verify_deadline)
    if settings.smtp_transport not in TRANSPORTS:
        return ProbeResult(skipped=True, detail=f"unknown SMTP_TRANSPORT '{settings.smtp_transport}'")
    if settings.smtp_transport == "disabled":
        return ProbeResult(
            skipped=True,
            detail="SMTP check disabled (outbound port 25 is blocked on this network). "
            "Set SMTP_TRANSPORT=direct or socks to enable.",
        )

    errors = []
    for host in mx_hosts[: max(1, settings.smtp_max_hosts)]:
        if deadline.expired():
            errors.append(f"overall deadline of {settings.verify_deadline:g}s reached before trying {host}")
            break
        result = _probe_host(host, email, settings, test_catch_all, deadline)
        if result.error is None:
            return result
        errors.append(f"{host}: {result.error}")
    return ProbeResult(error="; ".join(errors) or "no MX hosts", detail="could not connect to any MX host")


def _probe_host(host: str, email: str, settings: Settings, test_catch_all: bool, deadline: Deadline) -> ProbeResult:
    result = ProbeResult(host=host)
    log = result.responses
    smtp = _client(settings, deadline)
    try:
        code, msg = smtp.connect(host, settings.smtp_port)
        log.append(SmtpResponse("connect", code, _decode(msg)))
        if code != 220:
            result.detail = f"server greeting was {code}, not 220"
            return result

        _arm(smtp, settings, deadline)
        code, msg = smtp.ehlo(settings.helo_host)
        log.append(SmtpResponse("ehlo", code, _decode(msg)))
        if not 200 <= code < 300:
            _arm(smtp, settings, deadline)
            code, msg = smtp.helo(settings.helo_host)
            log.append(SmtpResponse("helo", code, _decode(msg)))
            if not 200 <= code < 300:
                result.detail = "server rejected EHLO and HELO"
                return result

        _arm(smtp, settings, deadline)
        code, msg = smtp.mail(settings.mail_from)
        log.append(SmtpResponse("mail_from", code, _decode(msg)))
        if not 200 <= code < 300:
            result.detail = "server rejected MAIL FROM"
            return result

        _arm(smtp, settings, deadline)
        code, msg = smtp.rcpt(email)
        result.rcpt = SmtpResponse("rcpt_to", code, _decode(msg))
        log.append(result.rcpt)

        if test_catch_all and 200 <= code < 300:
            domain = email.rsplit("@", 1)[1]
            result.random_address = f"{random_local_part()}@{domain}"
            _arm(smtp, settings, deadline)
            code, msg = smtp.rcpt(result.random_address)
            result.random_rcpt = SmtpResponse("rcpt_to_random", code, _decode(msg))
            log.append(result.random_rcpt)
        result.detail = f"probed {host}"
        return result
    except smtplib.SMTPServerDisconnected as e:
        if not log:
            result.error = f"disconnected before greeting: {e}"
        else:
            result.detail = f"server disconnected mid-session: {e}"
        return result
    except DeadlineExceeded as e:
        if not log:
            result.error = str(e)
        else:
            result.detail = f"{e} mid-session"
        return result
    except (socket.timeout, TimeoutError):
        if not log:
            result.error = f"connection timed out after {settings.smtp_timeout}s"
        else:
            result.detail = f"timed out after {settings.smtp_timeout}s mid-session"
        return result
    except (OSError, smtplib.SMTPException) as e:
        if not log:
            result.error = f"{type(e).__name__}: {e}"
        else:
            result.detail = f"{type(e).__name__} mid-session: {e}"
        return result
    finally:
        _quit(smtp, log)


def _quit(smtp: smtplib.SMTP, log: list[SmtpResponse]) -> None:
    if smtp.sock is None:
        return
    try:
        smtp.sock.settimeout(2)  # QUIT is a courtesy; don't let it stall past the deadline
        code, msg = smtp.docmd("QUIT")
        log.append(SmtpResponse("quit", code, _decode(msg)))
    except (OSError, smtplib.SMTPException):
        pass
    finally:
        smtp.close()
