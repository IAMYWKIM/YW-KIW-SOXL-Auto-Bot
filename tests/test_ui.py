# -*- coding: utf-8 -*-
"""텔레그램 메뉴·설정·보고 화면 점검 (엔진·키움 없이 화면 생성과 버튼 처리만 확인)
  python3 tests/test_ui.py
"""
import asyncio
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
os.environ.update(TRADE_MODE="MOCK", DRY_RUN="true", TELEGRAM_ENABLED="false", TELEGRAM_CHAT_ID="1")
import config  # noqa: E402

_tmp = tempfile.mkdtemp()
config.DATA_DIR = type(config.DATA_DIR)(_tmp)
config.REPORT_DIR = config.DATA_DIR
config.CONFIG_JSON = config.DATA_DIR / "config.json"
config.ensure_config_json()
import store  # noqa: E402

store.DB_PATH = config.DATA_DIR / "t.db"
import export, reports, scheduler, settings_menu, telegram_bot  # noqa: E402,E401

fails = 0


def check(name, cond, extra=""):
    global fails
    print(("  ✅ " if cond else "  ❌ ") + name + (f"  {extra}" if extra else ""))
    fails += 0 if cond else 1


class FakeCtrl:
    def __init__(self):
        self.sent, self.passed = [], []

    async def _call_tg(self, *a, **k):
        return {"ok": True}

    async def send_message(self, text, chat_id=None, reply_markup=None, parse_mode="HTML"):
        self.sent.append((text, reply_markup))
        return True

    async def edit_or_send_message(self, chat_id, message_id, text, reply_markup=None):
        self.sent.append((text, reply_markup))
        return True

    async def _handle_command_or_text(self, chat_id, raw_text):
        self.passed.append(raw_text)


async def main():
    c = FakeCtrl()
    cb = lambda d: {"id": "1", "data": d, "message": {"message_id": 5, "chat": {"id": 1}}}  # noqa: E731
    check("/menu 처리", await telegram_bot.handle_text(c, "1", "/menu") and "키움 SOXL" in c.sent[-1][0])
    check("모르는 명령은 원본으로 넘김", not await telegram_bot.handle_text(c, "1", "/avwap"))
    check("다른 사람(chat_id) 차단", not await telegram_bot.handle_text(c, "999", "/menu"))
    check("원본 콜백은 건드리지 않음", not await telegram_bot.handle_callback(c, cb("toggle_bot_active")))
    for d in ("kx:menu", "kx:tiers", "kx:today", "kx:orders", "kx:events", "kx:perf", "kx:email", "kx:set",
              "kx:set:fee", "kx:set:safe", "kx:set:report", "kx:set:alert", "kx:set:kiwoom", "kx:ver",
              "kx:rep:day:0", "kx:rep:week:1", "kx:rep:month:1"):
        n = len(c.sent)
        ok = await telegram_bot.handle_callback(c, cb(d))
        kb = c.sent[-1][1]["inline_keyboard"] if len(c.sent) > n else []
        too_long = [b["callback_data"] for r in kb for b in r if len(b["callback_data"].encode()) > 64]
        check(f"버튼 {d}", ok and len(c.sent) > n and not too_long, f"{len(c.sent[-1][0])}자")
    await telegram_bot.handle_callback(c, cb("kx:eng:/avwap"))
    check("원본 화면 바로가기(레이더)", c.passed == ["/avwap"])
    before = config.get("REPORT.DAILY_ENABLED")
    await telegram_bot.handle_callback(c, cb("kx:tog:report:0"))
    check("토글 버튼으로 설정 변경", config.get("REPORT.DAILY_ENABLED") == (not before))
    await telegram_bot.handle_callback(c, cb("kx:val:safe:0:2"))
    check("값 버튼으로 설정 변경", config.get("SAFETY.MAX_BOT_BUYING_POWER_USD") == 10000)
    await telegram_bot.handle_text(c, "1", "/set SAFETY.MAX_ORDER_USD 3000")
    check("/set 명령", config.get("SAFETY.MAX_ORDER_USD") == 3000, c.sent[-1][0])

    # 원장 변화 → 기록 → 보고
    prev = {"total_buy_events": 3, "total_buy_shares": 60, "total_sell_events": 1, "total_sold_shares": 20,
            "cumulative_realized_net_usd": 5.0, "completed_main_cycles": 0, "is_active": True, "initial_seed_usd": 10000}
    cur = {**prev, "total_sell_events": 2, "total_sold_shares": 60, "cumulative_realized_net_usd": 17.5,
           "cumulative_realized_gross_usd": 20.0, "cumulative_realized_fee_usd": 2.5, "completed_main_cycles": 1}
    ev = scheduler.diff_ledger(prev, cur)
    check("원장 차이 → 매도·졸업 사건", [e["kind"] for e in ev] == ["SELL", "CYCLE_DONE"] and abs(ev[0]["realized_net"] - 12.5) < 1e-9)
    check("초기화 감지", scheduler.diff_ledger(cur, {**cur, "total_sell_events": 0, "total_buy_events": 0,
                                                 "cumulative_realized_net_usd": 0})[0]["kind"] == "RESET")
    store.add_order("T1", "000000001", "NEW", "SOXL", "BUY", 20, 30.0)
    store.add_fill("T1", "SOXL", "BUY", 20, 30.0, 1.5, 30.0, "DIP_1FL", context={"tiers": 2, "live_price": 30.0,
                   "buckets": [{"tier": 1, "qty": 20, "price": 30.0}], "indicators": {"rsi_14": 41.2, "vwap": 30.1}})
    store.add_fill("T2", "SOXL", "SELL", 20, 30.45, 1.52, 30.45, "SELL_L1_ONLY")
    for e in ev:
        store.add_ledger_event(e["kind"], qty=e.get("qty", 0), realized_net=e.get("realized_net", 0.0),
                               realized_gross=e.get("realized_gross", 0.0), fee=e.get("fee", 0.0), detail=e.get("detail"))
    store.add_snapshot(30.2, 0, 0, 0, 0, 10012.5, 10012.5, 12.5, 0)
    for kind in ("day", "week", "month"):
        s = reports.build(*reports.period_range(kind)[:2])
        txt = reports.format_text(kind, s=s, fx=1450.0)
        check(f"{kind} 보고 문구", s["realized"] == 12.5 and s["buy_n"] == 1 and s["cycles"] == 1 and "+$12.50" in txt)
        path = export.build_excel(kind)
        from openpyxl import load_workbook
        wb = load_workbook(path)
        check(f"{kind} 엑셀 9시트", len(wb.sheetnames) == 9 and wb["체결내역(복기)"].max_row == 3, path.name)
    print("\n결과:", "모두 통과" if fails == 0 else f"실패 {fails}건")
    return fails


if __name__ == "__main__":
    sys.exit(1 if asyncio.run(main()) else 0)
