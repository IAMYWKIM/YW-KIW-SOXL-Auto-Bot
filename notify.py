# -*- coding: utf-8 -*-
"""kiw_soxl_trader - 텔레그램 발송(보고서·파일·봇 자체 알림)

명령/버튼 수신(폴링)은 원본 엔진이 맡고, 여기서는 같은 봇 토큰으로 "보내기"만 합니다.
"""
import json
import time

import requests

import config
from logger import get_logger

log = get_logger("notify")
_last_sent = {}


def enabled() -> bool:
    return bool(config.TELEGRAM_ENABLED and config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID)


def _url(method: str) -> str:
    return f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/{method}"


def send(text: str, buttons=None, chat_id: str = None) -> bool:
    """HTML 메시지 발송. 4,000자를 넘으면 나눠 보냅니다. buttons: [[(글자, callback_data), ...], ...]"""
    if not enabled():
        log.info("[텔레그램 꺼짐] %s", text[:200])
        return False
    chunks, cur = [], ""
    for line in str(text).split("\n"):
        if len(cur) + len(line) + 1 > 3900:
            chunks.append(cur)
            cur = ""
        cur += line + "\n"
    chunks.append(cur)
    ok = True
    for i, ch in enumerate(chunks):
        payload = {"chat_id": chat_id or config.TELEGRAM_CHAT_ID, "text": ch, "parse_mode": "HTML",
                   "disable_web_page_preview": True}
        if buttons and i == len(chunks) - 1:
            payload["reply_markup"] = {"inline_keyboard": [[{"text": t, "callback_data": d[:64]} for t, d in row]
                                                           for row in buttons]}
        try:
            r = requests.post(_url("sendMessage"), json=payload, timeout=10)
            if not r.ok:
                payload.pop("parse_mode", None)
                r = requests.post(_url("sendMessage"), json=payload, timeout=10)
            ok = ok and r.ok
        except Exception as e:
            log.warning("텔레그램 발송 실패: %s", e)
            ok = False
    return ok


def send_document(path, caption: str = "", chat_id: str = None) -> bool:
    if not enabled():
        return False
    try:
        with open(path, "rb") as f:
            r = requests.post(_url("sendDocument"), data={"chat_id": chat_id or config.TELEGRAM_CHAT_ID,
                                                          "caption": caption[:1000]},
                              files={"document": (str(getattr(path, "name", path)).split("/")[-1], f)}, timeout=60)
        return r.ok
    except Exception as e:
        log.warning("텔레그램 파일 발송 실패: %s", e)
        return False


def alert_once(key: str, text: str, cooldown_min: float = 30) -> bool:
    """같은 종류의 알림은 cooldown_min 분에 한 번만"""
    now = time.time()
    if now - _last_sent.get(key, 0) < cooldown_min * 60:
        return False
    _last_sent[key] = now
    return send(text)


def dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=1)[:3500]
