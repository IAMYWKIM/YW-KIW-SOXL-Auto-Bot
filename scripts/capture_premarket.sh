#!/bin/bash
# 프리장 키움 시세 원본 기록 (조회 전용 · 주문 없음)
# 04:07~04:30 ET 동안 2초마다 현재가(usa20100)·호가(usa20101)·체결틱(usa20150) 원본 응답을 그대로 저장합니다.
# 실행: bash ~/capture_premarket.sh   → 결과: ~/kiw_capture_YYYYMMDD.jsonl
cd ~/kiw_soxl_trader || exit 1
nohup kiw_soxl_venv/bin/python3 - > ~/kiw_capture.log 2>&1 <<'PY' &
import json, time
from datetime import datetime
from zoneinfo import ZoneInfo
from kiwoom_us_client import KiwoomUsClient, TR_QUOTE, TR_ORDERBOOK, TR_TICKS, PATH_MRKCOND
ET = ZoneInfo("America/New_York")
k = KiwoomUsClient()
body = {"stex_tp": k.stex("SOXL"), "stk_cd": "SOXL"}
def hm(): return int(datetime.now(ET).strftime("%H%M"))
while not (407 <= hm() < 430):
    time.sleep(10)
out = open(f"/home/{__import__('getpass').getuser()}/kiw_capture_{datetime.now(ET):%Y%m%d}.jsonl", "a")
print("기록 시작", datetime.now(ET), flush=True)
while hm() < 430:
    rec = {"t": datetime.now(ET).isoformat(timespec="seconds")}
    for name, tr in (("quote", TR_QUOTE), ("book", TR_ORDERBOOK), ("ticks", TR_TICKS)):
        try:
            rec[name] = k.request(tr, PATH_MRKCOND, body)
        except Exception as e:
            rec[name] = {"error": str(e)}
        time.sleep(0.3)
    out.write(json.dumps(rec, ensure_ascii=False) + "\n"); out.flush()
    time.sleep(1.2)
print("기록 종료", datetime.now(ET), flush=True)
PY
echo "✅ 대기 시작 — 04:07 ET(한국 18:07)부터 04:30 ET(18:30)까지 자동 기록합니다. 진행: cat ~/kiw_capture.log"
