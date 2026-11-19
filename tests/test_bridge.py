# -*- coding: utf-8 -*-
"""브릿지 단위 점검: 가짜 키움 서버를 상대로 토스 형식 응답과 주문 흐름을 확인합니다.
  python3 tests/test_bridge.py
"""
import asyncio
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
os.environ.update(TRADE_MODE="MOCK", KIWOOM_APP_KEY_MOCK="k", KIWOOM_SECRET_KEY_MOCK="s", DRY_RUN="false",
                  KIWOOM_MOCK_API_BASE="http://127.0.0.1:18081", TELEGRAM_ENABLED="false")
import config  # noqa: E402

_tmp = tempfile.mkdtemp()
config.DATA_DIR = type(config.DATA_DIR)(_tmp)
import store  # noqa: E402

store.DB_PATH = config.DATA_DIR / "t.db"
import fake_kiwoom  # noqa: E402
from toss_bridge import TossBridge  # noqa: E402

fake_kiwoom.serve(18081)
M = fake_kiwoom.MKT


class Err(Exception):
    def __init__(self, status, code, msg, raw=None):
        self.status_code, self.error_code, self.message = status, code, msg
        super().__init__(f"{status} {code} {msg}")


def check(name, cond, extra=""):
    print(("  ✅ " if cond else "  ❌ ") + name + (f"  {extra}" if extra else ""))
    if not cond:
        check.failed += 1


check.failed = 0


async def run(dry: bool):
    print(f"── {'DRY_RUN 가상계좌' if dry else '주문 실경로(가짜 서버)'} ──")
    b = TossBridge(error_cls=Err, dry_run=dry)
    b.paper_reset(10000)
    M.prices["SOXL"] = 30.0
    r = (await b.request("GET", "/api/v1/prices", params={"symbols": "SOXL,QQQ"}))["result"]
    check("현재가 2종목", len(r) == 2 and float(r[0]["lastPrice"]) == 30.0, str(r[0]))
    r = (await b.request("GET", "/api/v1/candles", params={"symbol": "SOXL", "interval": "1m", "count": 200}))["result"]
    check("1분봉 200개 + 다음 페이지 커서", len(r["candles"]) == 200 and r["nextBefore"], r["candles"][-1]["timestamp"])
    r2 = (await b.request("GET", "/api/v1/candles", params={"symbol": "SOXL", "interval": "1m", "count": 200,
                                                           "before": r["nextBefore"]}))["result"]
    check("2페이지가 1페이지보다 과거", r2["candles"] and r2["candles"][-1]["timestamp"] < r["candles"][0]["timestamp"])
    r = (await b.request("GET", "/api/v1/candles", params={"symbol": "SOXL", "interval": "1d", "count": 10}))["result"]
    check("일봉 10개 (날짜 오름차순)", len(r["candles"]) == 10 and r["candles"][0]["timestamp"] < r["candles"][-1]["timestamp"])
    from datetime import datetime as _d, timedelta as _t
    from zoneinfo import ZoneInfo as _Z
    last_d = r["candles"][-1]["timestamp"][:10]
    check("최신 일봉이 최근 날짜(기준일=오늘)", last_d >= (_d.now(_Z("America/New_York")) - _t(days=4)).strftime("%Y-%m-%d"), last_d)
    from kiwoom_us_client import parse_ts14
    check("24시 이상 표기 변환", str(parse_ts14("20261110263300")) == "2026-11-11 02:33:00")
    age = b.newest_candle_age_min("SOXL")
    _wk = __import__("datetime").datetime.now(__import__("zoneinfo").ZoneInfo("America/New_York")).weekday()
    if _wk < 5:   # 주말에는 금요일 봉이 최신이라 검사하지 않음
        check("최신 1분봉이 지금 시각과 일치", age is not None and abs(age) < 2, f"{age:+.1f}분" if age is not None else "")
    else:
        print("  ·  최신 1분봉 시각 검사: 주말이라 건너뜀")
    r = (await b.request("GET", "/api/v1/orderbook", params={"symbol": "SOXL"}))["result"]
    check("호가", float(r["bids"][0]["price"]) < float(r["asks"][0]["price"]))
    r = (await b.request("GET", "/api/v1/trades", params={"symbol": "SOXL", "count": 15}))["result"]
    check("체결내역 시각 ISO", bool(r) and "T" in r[0]["timestamp"])
    r = (await b.request("GET", "/api/v1/exchange-rate", params={}))["result"]
    check("환율", float(r["rate"]) > 1000)
    r = (await b.request("GET", "/api/v1/commissions"))["result"]
    check("수수료율", r[0]["marketCountry"] == "US" and 0 < float(r[0]["commissionRate"]) <= 0.01)
    bp0 = float((await b.request("GET", "/api/v1/buying-power", params={"currency": "USD"}))["result"]["cashBuyingPower"])
    check("주문가능금액", bp0 > 0, f"${bp0:,.2f}")

    # 매수: 지정가가 현재가보다 낮으면 대기 → 가격 하락 시 체결
    oid = (await b.request("POST", "/api/v1/orders", json_data={"symbol": "SOXL", "side": "BUY", "quantity": "10",
                                                              "price": "29.50", "clientOrderId": "t1"}))["result"]["orderId"]
    d = (await b.request("GET", f"/api/v1/orders/{oid}"))["result"]
    check("매수 접수 → PENDING", d["status"] == "PENDING" and d["execution"]["filledQuantity"] == "0", d["status"])
    r = (await b.request("GET", "/api/v1/orders", params={"status": "OPEN", "symbol": "SOXL"}))["result"]["orders"]
    check("미체결 목록에 표시", any(o["orderId"] == oid for o in r))
    M.prices["SOXL"] = 29.40
    b._drop("q:", "ord:")
    await asyncio.sleep(0.5)
    d = (await b.request("GET", f"/api/v1/orders/{oid}"))["result"]
    check("가격 하락 → FILLED", d["status"] == "FILLED" and d["execution"]["filledQuantity"] == "10", str(d["execution"]))
    h = (await b.request("GET", "/api/v1/holdings"))["result"]["items"]
    check("보유 10주", h and h[0]["symbol"] == "SOXL" and h[0]["quantity"] == "10", str(h[:1]))
    sq = (await b.request("GET", "/api/v1/sellable-quantity", params={"symbol": "SOXL"}))["result"]["sellableQuantity"]
    check("매도가능 10주", sq == "10")
    check("체결 기록 1건", len(store.query("SELECT * FROM fills WHERE order_id=?", (oid,))) == 1)

    # 매도 대기 → 취소
    sid = (await b.request("POST", "/api/v1/orders", json_data={"symbol": "SOXL", "side": "SELL", "quantity": "10",
                                                              "price": "31.00"}))["result"]["orderId"]
    await b.request("POST", f"/api/v1/orders/{sid}/cancel", json_data={})
    b._drop("ord:")
    await asyncio.sleep(0.4)
    d = (await b.request("GET", f"/api/v1/orders/{sid}"))["result"]
    check("취소 → CANCELED (체결 0)", d["status"] == "CANCELED" and d["execution"]["filledQuantity"] == "0", d["status"])

    # 매도 → 가격 상승 시 체결
    sid = (await b.request("POST", "/api/v1/orders", json_data={"symbol": "SOXL", "side": "SELL", "quantity": "10",
                                                              "price": "29.90"}))["result"]["orderId"]
    M.prices["SOXL"] = 30.10
    b._drop("q:", "ord:")
    await asyncio.sleep(0.5)
    d = (await b.request("GET", f"/api/v1/orders/{sid}"))["result"]
    check("매도 FILLED", d["status"] == "FILLED", str(d["execution"]))
    r = (await b.request("GET", "/api/v1/orders", params={"status": "CLOSED", "symbol": "SOXL", "limit": 30}))["result"]["orders"]
    ids = [o["orderId"] for o in r]
    check("종료 주문 목록에 매수·매도 포함", oid in ids and sid in ids, f"{len(ids)}건")
    h = (await b.request("GET", "/api/v1/holdings"))["result"]["items"]
    check("전량 매도 후 보유 0", not h)

    # 오류 번역
    try:
        await b.request("POST", "/api/v1/orders", json_data={"symbol": "SOXL", "side": "BUY", "quantity": "100000",
                                                           "price": "30.00"})
        check("예수금 부족 오류", False)
    except Err as e:
        check("예수금 부족 → insufficient-buying-power", e.error_code == "insufficient-buying-power", e.error_code)
    try:
        await b.request("GET", "/api/v1/unknown-new-endpoint")
        check("미등록 주소 오류", False)
    except Err as e:
        check("미등록 토스 주소 → 명확한 오류", e.error_code == "kiwoom-bridge-unmapped-endpoint")


async def main():
    await run(dry=False)
    store._exec("DELETE FROM orders")
    store._exec("DELETE FROM fills")
    await run(dry=True)
    # 키움 일시 오류(return_code 7, [1631]) 대응
    import kiwoom_us_client as kuc
    kuc.time.sleep = lambda *_: None
    b2 = TossBridge(error_cls=Err, dry_run=True)
    M.fail["usa06012"] = 1
    r = (await b2.request("GET", "/api/v1/candles", params={"symbol": "SOXL", "interval": "1d", "count": 5}))["result"]
    check("일시 오류 1회 → 자동 재시도로 성공", len(r["candles"]) == 5)
    b2._cache["d1:SOXL"] = (b2._cache["d1:SOXL"][0] - 400, b2._cache["d1:SOXL"][1])   # 캐시 만료시킴
    M.fail["usa06012"] = 5
    r = (await b2.request("GET", "/api/v1/candles", params={"symbol": "SOXL", "interval": "1d", "count": 5}))["result"]
    check("계속 실패 → 직전 일봉으로 이어 감", len(r["candles"]) == 5)
    M.fail["usa06012"] = 0
    n0 = M.calls.count("ust20000")
    M.fail["ust20000"] = 1
    b3 = TossBridge(error_cls=Err, dry_run=False)
    try:
        await b3.request("POST", "/api/v1/orders", json_data={"symbol": "SOXL", "side": "BUY", "quantity": "1", "price": "1.00"})
        check("주문은 재시도 안 함", False)
    except Err:
        check("주문은 일시 오류여도 재시도 안 함(중복 주문 방지)", M.calls.count("ust20000") - n0 == 1)
    import scheduler
    sent = []
    import notify as _n
    _n.alert_once = lambda key, text, cd=30: sent.append(text) or True

    class _L:
        def run_in_executor(self, ex, fn, *a):
            fn(*a)
    h = scheduler.make_api_error_handler(_L())
    h("usa06012", "kiwoom-7", "일시", True)
    h("usa06012", "kiwoom-7", "일시", True)
    check("일시 오류 1~2회는 알림 안 함", not sent)
    h("usa06012", "kiwoom-7", "일시", True)
    check("10분 내 3회째에 알림", len(sent) == 1)
    h("ust20000", "insufficient-buying-power", "부족", False)
    check("일반 오류는 바로 알림", len(sent) == 2)

    # 인증 오류는 'NO_STEX' 로 덮이지 않고 원인 안내와 함께 올라와야 함
    from kiwoom_us_client import KiwoomUsClient, KiwoomApiError, hint_for
    bad = KiwoomUsClient(base_url="http://127.0.0.1:18081", app_key="", secret_key="")
    try:
        bad.stex("SPY")
        check("빈 키 오류", False)
    except KiwoomApiError as e:
        check("인증 오류가 그대로 보임(NO_STEX 아님)", e.code == "NO_KEY", str(e)[:80])
    check("오류코드 안내 8050", "IP" in hint_for("8050"))
    k = KiwoomUsClient()
    check("SOXL 거래소구분 = NY", k.stex("SOXL") == "NY")
    check("미지정 종목 자동 탐색", k.stex("TSLA") == "NY")
    print("\n결과:", "모두 통과" if check.failed == 0 else f"실패 {check.failed}건")
    return check.failed


if __name__ == "__main__":
    sys.exit(1 if asyncio.run(main()) else 0)
