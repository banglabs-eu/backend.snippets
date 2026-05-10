"""Minimal SMTP helper for outbound email.

If SMTP_HOST is not configured, links are logged to stdout instead of sent —
useful for local development.

For Brevo (formerly Sendinblue) transactional SMTP, set:

    SMTP_HOST=smtp-relay.brevo.com
    SMTP_PORT=587
    SMTP_USER=<your brevo SMTP login, usually an email>
    SMTP_PASS=<the SMTP key from brevo dashboard — *not* the API key>
    SMTP_FROM=<a verified sender on your account, e.g. noreply@snippets.eu>

Note: Brevo distinguishes between "API keys" (for the v3 transactional API)
and "SMTP keys" (for this relay). Generate the SMTP key under SMTP & API → SMTP.
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
    """The link works for both sign-in (existing account) and sign-up
    (new email — verify step will prompt for a username). Copy stays generic
    so the same email body covers both paths."""
    send_email(
        to,
        "Your Snippets link",
        (
            "Click the link below to continue to Snippets. It expires in 10 "
            "minutes and can only be used once.\n\n"
            f"{link}\n\n"
            "If you don't have an account yet, this link will set one up; if "
            "you do, it'll sign you in.\n\n"
            "If you didn't request this, you can ignore this email."
        ),
    )
