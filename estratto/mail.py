from __future__ import annotations

import re
import smtplib
from email.message import EmailMessage

from .config import Config


def is_mail_configured(cfg: Config) -> bool:
    return bool(
        cfg.mail_host
        and cfg.mail_user
        and cfg.mail_pass
        and cfg.mail_from
    )


def _envelope_from_address(cfg: Config) -> str:
    raw_from = cfg.mail_from
    match = re.search(r"<([^<>@\s]+@[^<>@\s]+)>", raw_from)
    if match:
        return match.group(1).strip()
    if re.fullmatch(r"[^<>\s@]+@[^<>\s@]+", raw_from):
        return raw_from.strip()
    return cfg.mail_user


def send_email(cfg: Config, *, to: str, subject: str, text: str) -> None:
    if not is_mail_configured(cfg):
        raise RuntimeError("Mail delivery is not configured.")

    msg = EmailMessage()
    msg["From"] = cfg.mail_from
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(text)

    smtp_cls = smtplib.SMTP_SSL if cfg.mail_secure else smtplib.SMTP
    with smtp_cls(cfg.mail_host, cfg.mail_port, timeout=20) as smtp:
        smtp.ehlo()
        if not cfg.mail_secure:
            smtp.starttls()
            smtp.ehlo()
        smtp.login(cfg.mail_user, cfg.mail_pass)
        smtp.sendmail(_envelope_from_address(cfg), [to], msg.as_string())
