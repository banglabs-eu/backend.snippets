"""Minimal SMTP helper for outbound email.

If SMTP_HOST is not configured, links are logged to stdout instead of sent —
useful for local development.
"""

import logging
import os
import smtplib
from email.message import EmailMessage


log = logging.getLogger(__name__)


def _smtp_config() -> dict:
    return {
        "host": os.environ.get("SMTP_HOST", "").strip(),
        "port": int(os.environ.get("SMTP_PORT", "587") or "587"),
        "user": os.environ.get("SMTP_USER", "").strip(),
        "password": os.environ.get("SMTP_PASS", "").strip(),
        "sender": os.environ.get("SMTP_FROM", "noreply@localhost").strip(),
    }


def send_email(to: str, subject: str, body: str) -> None:
    cfg = _smtp_config()
    if not cfg["host"]:
        log.warning("SMTP_HOST not set — would have sent email to %s\n%s\n\n%s", to, subject, body)
        return

    msg = EmailMessage()
    msg["From"] = cfg["sender"]
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)

    with smtplib.SMTP(cfg["host"], cfg["port"]) as smtp:
        smtp.starttls()
        if cfg["user"]:
            smtp.login(cfg["user"], cfg["password"])
        smtp.send_message(msg)


def send_magic_link(to: str, link: str) -> None:
    send_email(
        to,
        "Your Snippets sign-in link",
        (
            "Click the link below to sign in to Snippets. It expires in 10 minutes "
            "and can only be used once.\n\n"
            f"{link}\n\n"
            "If you didn't request this, you can ignore this email."
        ),
    )
