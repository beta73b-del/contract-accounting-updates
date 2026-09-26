# -*- coding: utf-8 -*-
"""Почтовые уведомления о подписании контрактов.

Gmail App Password передаётся сюда вызывающим кодом. Начиная с v2.10.12
он хранится в облачной базе приложения, чтобы быть доступным на другом ПК.
"""
from __future__ import annotations

import re
import smtplib
from email.message import EmailMessage
from typing import Iterable

GMAIL_HOST = "smtp.gmail.com"
GMAIL_PORT = 465


class EmailConfigError(RuntimeError):
    pass


def normalize_email(value: str) -> str:
    return (value or "").strip().lower()


def parse_recipients(value: str) -> list[str]:
    parts = re.split(r"[;,\s]+", value or "")
    return [normalize_email(p) for p in parts if p.strip()]


def validate_email(value: str) -> bool:
    value = normalize_email(value)
    return bool(re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value))


def send_gmail(sender: str, recipients: Iterable[str], subject: str, body: str,
               app_password: str | None = None, timeout: int = 20) -> None:
    sender = normalize_email(sender)
    recips = [normalize_email(x) for x in recipients if normalize_email(x)]
    if not validate_email(sender):
        raise EmailConfigError("Некорректный адрес Gmail отправителя.")
    if not recips or any(not validate_email(x) for x in recips):
        raise EmailConfigError("Некорректный адрес получателя.")
    password = (app_password or "").replace(" ", "")
    if not password:
        raise EmailConfigError(
            "Gmail App Password не сохранён в облачных настройках. Откройте «Настройки → Почта…»."
        )

    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = ", ".join(recips)
    msg["Subject"] = subject
    msg.set_content(body)

    try:
        with smtplib.SMTP_SSL(GMAIL_HOST, GMAIL_PORT, timeout=timeout) as smtp:
            smtp.login(sender, password.replace(" ", ""))
            smtp.send_message(msg)
    except smtplib.SMTPAuthenticationError as exc:
        raise EmailConfigError(
            "Gmail отклонил авторизацию. Проверьте адрес и App Password (не обычный пароль Gmail)."
        ) from exc
    except OSError as exc:
        raise EmailConfigError(f"Не удалось подключиться к Gmail: {exc}") from exc
    except smtplib.SMTPException as exc:
        raise EmailConfigError(f"Ошибка отправки Gmail: {exc}") from exc