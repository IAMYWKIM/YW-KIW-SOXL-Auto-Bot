# -*- coding: utf-8 -*-
"""kiw_soxl_trader - 성과보고 이메일 (SMTP: Gmail 앱 비밀번호 / 네이버 등). kiw_scalp_trader 와 같은 .env 항목."""
import html
import re
import smtplib
import ssl
from email.message import EmailMessage

import config
from logger import get_logger

log = get_logger("email")


def configured() -> bool:
    return bool(config.SMTP_USER and config.SMTP_PASSWORD and config.EMAIL_TO
                and "앱비밀번호" not in config.SMTP_PASSWORD and "@" in config.SMTP_USER
                and "보내는계정" not in config.SMTP_USER)


def auto_enabled(kind: str) -> bool:
    key = {"day": "DAILY", "week": "WEEKLY", "month": "MONTHLY"}.get(kind, "DAILY")
    return bool(config.get("EMAIL.ENABLED", False)) and bool(config.get(f"EMAIL.{key}", True)) and configured()


def send(subject: str, text: str, attachments=None) -> tuple:
    """반환 (성공 여부, 설명)"""
    if not configured():
        return False, "이메일 계정이 설정되지 않았습니다 (.env 의 SMTP_USER / SMTP_PASSWORD / EMAIL_TO)"
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = config.SMTP_USER
    msg["To"] = ", ".join(config.EMAIL_TO)
    plain = re.sub(r"<[^>]+>", "", text)
    msg.set_content(plain)
    body = html.escape(plain).replace("\n", "<br>")
    for m in re.findall(r"<b>(.*?)</b>", text):
        body = body.replace(html.escape(re.sub(r"<[^>]+>", "", m)), f"<b>{html.escape(re.sub(r'<[^>]+>', '', m))}</b>", 1)
    msg.add_alternative(f"<div style='font-family:Malgun Gothic,Apple SD Gothic Neo,sans-serif;font-size:14px;"
                        f"line-height:1.6'>{body}</div>", subtype="html")
    for p in attachments or []:
        try:
            with open(p, "rb") as f:
                msg.add_attachment(f.read(), maintype="application",
                                   subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                   filename=str(p).split("/")[-1])
        except Exception as e:
            log.warning("첨부 실패 %s: %s", p, e)
    try:
        if int(config.SMTP_PORT) == 465:
            with smtplib.SMTP_SSL(config.SMTP_HOST, 465, context=ssl.create_default_context(), timeout=30) as s:
                s.login(config.SMTP_USER, config.SMTP_PASSWORD.replace(" ", ""))
                s.send_message(msg)
        else:
            with smtplib.SMTP(config.SMTP_HOST, int(config.SMTP_PORT), timeout=30) as s:
                s.starttls(context=ssl.create_default_context())
                s.login(config.SMTP_USER, config.SMTP_PASSWORD.replace(" ", ""))
                s.send_message(msg)
        log.info("이메일 발송 완료: %s → %s", subject, config.EMAIL_TO)
        return True, f"발송 완료 → {', '.join(config.EMAIL_TO)}"
    except Exception as e:
        log.warning("이메일 발송 실패: %s", e)
        return False, f"발송 실패: {e}"
