"""Outgoing e-mail (password reset codes).

SMTP is used when SMTP_HOST is configured. Otherwise the console mailer
writes the message to the server log (development / demo without a mail
server) and keeps it in memory so tests can read it.
"""
from __future__ import annotations

import asyncio
import logging
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Mail:
    to: str
    subject: str
    body: str


class ConsoleMailer:
    def __init__(self) -> None:
        self.outbox: list[Mail] = []

    async def send(self, to: str, subject: str, body: str) -> None:
        self.outbox = (self.outbox + [Mail(to, subject, body)])[-50:]
        log.warning("[email:console] To: %s | %s | %s", to, subject, body.replace("\n", " "))


class SmtpMailer:
    def __init__(self, host: str, port: int, sender: str, username: str | None,
                 password: str | None, starttls: bool = True) -> None:
        self._host, self._port, self._sender = host, port, sender
        self._username, self._password, self._starttls = username, password, starttls

    def _send_sync(self, msg: EmailMessage) -> None:
        with smtplib.SMTP(self._host, self._port, timeout=15) as smtp:
            if self._starttls:
                smtp.starttls()
            if self._username:
                smtp.login(self._username, self._password or "")
            smtp.send_message(msg)

    async def send(self, to: str, subject: str, body: str) -> None:
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = self._sender, to, subject
        msg.set_content(body)
        try:
            await asyncio.to_thread(self._send_sync, msg)
        except (OSError, smtplib.SMTPException):
            log.exception("Could not send e-mail to %s", to)


def build_mailer(settings):
    if settings.smtp_host:
        return SmtpMailer(settings.smtp_host, settings.smtp_port, settings.mail_from,
                          settings.smtp_username, settings.smtp_password, settings.smtp_starttls)
    return ConsoleMailer()
