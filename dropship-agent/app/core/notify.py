"""Notifications: always saved for the dashboard; optionally emailed to YOU.

Email goes only to `notifications.email_to` in config.yaml (your own address). It is never used to
contact suppliers or customers: those messages are drafts you send yourself.
"""

from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage

from sqlalchemy.orm import Session

from app.core.logger import log_event
from app.core.models import Notification


def notify(session: Session, *, kind: str, title: str, body: str | None = None, link: str | None = None,
           settings=None) -> Notification:
    n = Notification(kind=kind, title=title, body=body, link=link)
    session.add(n)
    session.commit()
    if settings is not None and settings.notifications.email_enabled:
        try:
            send_email(settings, title, (body or "") + (f"\n\nOpen: http://127.0.0.1:8000{link}" if link else ""))
            n.emailed = True
            session.commit()
        except (OSError, smtplib.SMTPException, ValueError) as exc:
            log_event(session, module="notify", action="email_failed", level="WARNING",
                      message=f"Couldn't email '{title}': {exc}", dry_run=settings.dry_run)
    return n


def send_email(settings, subject: str, body: str) -> None:
    c = settings.notifications
    if not (c.email_to and c.smtp_host and c.smtp_user and settings.smtp_password):
        raise ValueError("Email isn't fully set up (email_to, smtp_host, smtp_user in config.yaml; SMTP_PASSWORD in .env).")
    msg = EmailMessage()
    msg["Subject"] = f"[Dropship Agent] {subject}"
    msg["From"] = c.email_from or c.smtp_user
    msg["To"] = c.email_to
    msg.set_content(body)
    with smtplib.SMTP(c.smtp_host, c.smtp_port, timeout=30) as smtp:
        smtp.starttls(context=ssl.create_default_context())
        smtp.login(c.smtp_user, settings.smtp_password)
        smtp.send_message(msg)


def unread_count(session: Session) -> int:
    from sqlalchemy import func, select

    return session.scalar(select(func.count()).select_from(Notification).where(Notification.read_at.is_(None))) or 0
