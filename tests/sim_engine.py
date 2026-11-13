# -*- coding: utf-8 -*-
"""
엔진 통합 시험 (원본 갱신·브릿지 수정 후 반드시 실행)

원본 엔진 + 키움 브릿지 + 기록/보고 모듈을 '가짜 키움 서버'에 붙여 실제로 돌려 봅니다.
  - 봇 폴더를 임시 폴더로 복사해서 돌리므로 서버의 원장·기록·설정은 건드리지 않습니다.
  - 시계를 미국 동부 평일 10:00 로 옮기고, SOXL 가격을 $30.00 → (75초) $30.45 → (90초) $30.55 → (110초~) $30.40 으로 움직입니다.
  - 기대 결과: 1층 매수(REENTRY_1FL) → 60초 타임쉴드 → +1% 점화·트레일링 콜백 익절(Case 99) → 0주 졸업(CYCLE_DONE),
              기동 직후 🔫 레이더 화면 1회 발송, 오류(Traceback) 0건.

  사용법:  kiw_soxl_venv/bin/python3 tests/sim_engine.py            # 약 2분 30초
           SIM_SEC=60 kiw_soxl_venv/bin/python3 tests/sim_engine.py  # 짧게 (매도 확인 없이 기동만)
"""
import asyncio
import datetime as _dt
import json
import os
import re
import shutil
import sys
import tempfile
import time

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORK = tempfile.mkdtemp(prefix="kiw_soxl_sim_")
PROJ = os.path.join(WORK, "bot")
shutil.copytree(SRC, PROJ, ignore=shutil.ignore_patterns("data", "run", "logs", "kiw_soxl_venv", "__pycache__",
                                                          ".env", "config.json", "*.zip"))
sys.path.insert(0, PROJ)
sys.path.insert(1, os.path.join(PROJ, "tests"))
os.chdir(PROJ)
PORT = int(os.environ.get("SIM_PORT", "18199"))
RUN_SEC = float(os.environ.get("SIM_SEC", "160"))
os.environ.update(TRADE_MODE="MOCK", KIWOOM_APP_KEY_MOCK="k", KIWOOM_SECRET_KEY_MOCK="s", DRY_RUN="false",
                  KIWOOM_MOCK_API_BASE=f"http://127.0.0.1:{PORT}", TELEGRAM_ENABLED="true",
                  TELEGRAM_BOT_TOKEN="123:SIM", TELEGRAM_CHAT_ID="1", LOG_LEVEL="WARNING")

_real = _dt.datetime
now_et = _real.now(_dt.timezone(_dt.timedelta(hours=-5)))
target = now_et.replace(hour=10, minute=0, second=0, microsecond=0)
while target.weekday() >= 5:
    target += _dt.timedelta(days=1)
OFFSET = target - now_et


class ShiftedDT(_real):
    @classmethod
    def now(cls, tz=None):
        return _real.now(tz) + OFFSET


import config  # noqa: E402
import fake_kiwoom  # noqa: E402

config.ensure_config_json()
fake_kiwoom.serve(PORT)
T0 = time.time()
# v18(Case 99) 1층 트레일링: +1.0% 점화 → 고점 +1.83% → 고점 대비 -0.49% 콜백(+1.33%, 보존선 +1.25% 위) 에서 매도
def _script(t):
    d = t - T0
    return 30.00 if d < 75 else 30.45 if d < 90 else 30.55 if d < 110 else 30.40
fake_kiwoom.MKT.script = _script

import engine_hooks  # noqa: E402
import scheduler  # noqa: E402
import store  # noqa: E402
import trade_context  # noqa: E402
from toss_bridge import TossBridge  # noqa: E402


async def amain() -> int:
    engine_hooks.load_engine()
    import notify
    import reports
    import telegram_bot
    import toss_bridge
    for mod in list(engine_hooks.runtime.modules.values()) + [toss_bridge, store, scheduler, reports, telegram_bot,
                                                              fake_kiwoom]:
        if getattr(mod, "datetime", None) is _real:
            mod.datetime = ShiftedDT
    bridge = TossBridge()
    bridge.context_provider = trade_context.snapshot
    engine_hooks.install(bridge)
    sent = []

    async def _fake_call(self, method, payload=None, *a, **k):
        if method in ("sendMessage", "editMessageText"):
            sent.append((payload or {}).get("text", ""))
        return {"ok": True, "result": {"message_id": 1}}

    async def _no_poll(self, *a, **k):
        await asyncio.sleep(3600)
    for cls in (engine_hooks.runtime.modules["tg_ui_plugin"].TelegramCommandController,
                engine_hooks.runtime.modules["main_runtime"]._AdaptiveTelegramBridge):
        cls._call_tg = _fake_call
    engine_hooks.runtime.modules["main_runtime"]._AdaptiveTelegramBridge.run_polling_loop = _no_poll
    notify.send = lambda text, *a, **k: sent.append(text) or True
    notify.send_document = lambda *a, **k: True
    tasks = scheduler.start_all()
    eng = asyncio.create_task(engine_hooks.run_engine())
    print(f"엔진 시험 {RUN_SEC:.0f}초 진행 중... (임시 폴더 {WORK})")
    await asyncio.sleep(RUN_SEC)

    ctx = engine_hooks.runtime.context
    st = ctx.ledger.get_state("SOXL") if ctx else {}
    kinds = [e["kind"] for e in store.ledger_between("2000-01-01", "2100-01-01")]
    fills = store.fills_between("2000-01-01", "2100-01-01")
    plain = [re.sub(r"<[^>]+>", "", t) for t in sent]
    print("\n── 주문 ──")
    for o in store.recent_orders(20)[::-1]:
        print(f"  {o['placed_et'][11:]} {o['side']} {o['qty']}주 @{o['price']} → {o['status']}")
    print("── 원장 변화 ──", kinds)
    print("── 텔레그램 첫 줄 ──")
    for t in plain:
        print("  ", t.split("\n", 1)[0][:90])
    log_txt = open(os.path.join(PROJ, "logs", "kiw_soxl_trader.log"), encoding="utf-8").read() \
        if os.path.exists(os.path.join(PROJ, "logs", "kiw_soxl_trader.log")) else ""
    checks = [
        ("엔진 컨텍스트 연결", ctx is not None),
        ("기동 레이더 화면 발송", any("스나이퍼 레이더" in t for t in plain)),
        ("1층 매수 체결", any(f["side"] == "BUY" for f in fills)),
        ("체결 상황 스냅샷 기록", bool(fills) and len(json.loads(fills[0]["context"])) > 10),
    ]
    if RUN_SEC >= 130:
        checks += [("1층 트레일링 익절 매도", any(f["side"] == "SELL" for f in fills)),
                   ("0주 졸업 기록", "CYCLE_DONE" in kinds),
                   ("실현손익 > 0", float(st.get("cumulative_realized_net_usd") or 0) > 0)]
    checks.append(("Traceback 없음", "Traceback" not in log_txt))
    print("\n── 판정 ──")
    bad = 0
    for name, ok in checks:
        print(("  ✅ " if ok else "  ❌ ") + name)
        bad += 0 if ok else 1
    try:
        ctx.shutdown_event.set()
    except Exception:
        pass
    for t in tasks:
        t.cancel()
    try:
        await asyncio.wait_for(eng, 6)
    except Exception:
        pass
    print("\n결과:", "모두 통과" if not bad else f"실패 {bad}건 (임시 폴더의 logs/ 확인: {WORK})")
    return bad


if __name__ == "__main__":
    rc = asyncio.run(amain())
    if not rc:
        shutil.rmtree(WORK, ignore_errors=True)
    os._exit(1 if rc else 0)
