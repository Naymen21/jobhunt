"""A local fake mail server that behaves differently per recipient domain.

    normal.test    accepts alice@ and bob@, rejects everyone else with 550 5.1.1
    catchall.test  accepts every address
    grey.test      defers every RCPT with 451 (greylisting)
    blocked.test   rejects with 550 5.7.1 Spamhaus-style IP block
"""
from __future__ import annotations

import socket

from aiosmtpd.controller import Controller

KNOWN_USERS = {"alice@normal.test", "bob@normal.test"}


class Handler:
    def __init__(self):
        self.data_calls = 0
        self.rcpts: list[str] = []

    async def handle_RCPT(self, server, session, envelope, address, rcpt_options):
        self.rcpts.append(address)
        domain = address.rsplit("@", 1)[-1].lower()
        if domain == "catchall.test":
            return "250 2.1.5 OK"
        if domain == "grey.test":
            return "451 4.7.1 Greylisted, please try again later"
        if domain == "blocked.test":
            return "550 5.7.1 Service unavailable; client host blocked using Spamhaus"
        if address.lower() in KNOWN_USERS:
            return "250 2.1.5 OK"
        return "550 5.1.1 The email account that you tried to reach does not exist"

    async def handle_DATA(self, server, session, envelope):
        self.data_calls += 1  # tests assert this stays 0
        return "554 no thanks"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start() -> tuple[Controller, Handler, int]:
    handler = Handler()
    port = free_port()
    controller = Controller(handler, hostname="127.0.0.1", port=port)
    controller.start()
    return controller, handler, port
