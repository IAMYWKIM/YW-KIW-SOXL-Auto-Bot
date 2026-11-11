# -*- coding: utf-8 -*-
"""
kiw_soxl_trader - 키움 미국주식 API 실측 점검 (실거래 전에 반드시 1회 실행)

키움 명세만으로는 확정할 수 없는 항목을 실제 응답으로 확인합니다.
  python3 diag.py                # 조회만 (주문 없음)
  python3 diag.py --order-test   # 추가로 SOXL 1주를 현재가의 절반 가격에 매수 주문 → 즉시 취소 (체결되지 않는 가격)

확인 항목: 토큰, 종목별 거래소구분, 현재가/호가, 체결내역 시각 형식, 1분봉(개수·시간대·프리장 포함 여부),
          일봉, 달러 예수금, 보유 종목, 당일 주문 조회 필드, 환율.
끝에 config.json 에 넣을 권장값을 알려 줍니다.
"""
import collections
import json
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import config
from kiwoom_us_client import (parse_ts14, PATH_ACCOUNT, PATH_CHART, TR_MINUTE_CHART, TR_ORDERS_TODAY, KiwoomApiError,
                              KiwoomUsClient, rows_of)

ET, KST = ZoneInfo("America/New_York"), ZoneInfo("Asia/Seoul")


def show(title, fn):
    print(f"\n── {title} " + "─" * max(0, 56 - len(title)))
    try:
        return fn()
    except KiwoomApiError as e:
        print(f"   ❌ {e}")
    except Exception as e:
        print(f"   ❌ {type(e).__name__}: {e}")
    return None


def main() -> int:
    config.ensure_config_json()
    k = KiwoomUsClient()
    print(f"키움 서버: {k.base} · 모드 {'모의' if config.KIWOOM_IS_MOCK else '실전'}")
    print(f"지금: {datetime.now(KST):%Y-%m-%d %H:%M:%S} KST = {datetime.now(ET):%Y-%m-%d %H:%M:%S} ET")
    advice = {}

    def _ip():
        import requests
        ip = requests.get("https://api.ipify.org", timeout=5).text.strip()
        print(f"   이 서버의 공인 IP: {ip}  ← 키움 OpenAPI 포털(openapi.kiwoom.com)에 등록된 IP와 같아야 합니다")
    show("0. 서버 공인 IP", _ip)

    if show("1. 접근토큰", lambda: print("   ✅ 발급 성공, 만료", k._ensure_token() and k._token_expire)) is None and not k._token:
        return 1

    def _stex():
        for s in ("SOXL", "QQQ", "SGOV"):
            code = k.stex(s)
            advice[f"SYMBOLS.{s}"] = code
            print(f"   {s}: {code}")
    show("2. 거래소구분 (NA=AMEX·Arca, ND=NASDAQ, NY=NYSE)", _stex)

    def _quote():
        q = k.quote("SOXL")
        print(f"   SOXL 현재가 ${q['price']:.4f} · 전일종가 ${q['prev_close']:.4f} · 환율 {q['fx']}")
        ob = k.orderbook("SOXL")
        if ob["bids"] and ob["asks"]:
            print(f"   호가: 매수1 ${ob['bids'][0]['price']:.4f} / 매도1 ${ob['asks'][0]['price']:.4f} (호가시각 {ob['date']} {ob['time']})")
        else:
            print("   ⚠️ 호가가 비어 있습니다 (장외 시간이면 정상일 수 있음)")
        return q
    q = show("3. 현재가 · 10호가", _quote)

    def _ticks():
        t = k.ticks("SOXL")
        print(f"   {len(t)}건. 앞 3건: {t[:3]}")
        print("   ※ 시각(tm)이 HHMMSS 6자리이고 지금 시각과 맞는지 보세요. 형식이 다르면 엔진의 '윗꼬리 포착'만 꺼지고 매매는 정상입니다.")
    show("4. 상세 체결내역 (usa20150)", _ticks)

    def _minute():
        body = {"stex_tp": k.stex("SOXL"), "stk_cd": "SOXL", "tic_scope": "1", "upd_stkpc_tp": "1", "exrt_appl_tp": "0"}
        old = (datetime.now(ET) - timedelta(days=6)).strftime("%Y%m%d")
        d_old = k.request(TR_MINUTE_CHART, PATH_CHART, {**body, "strt_dt": old})
        r_old = sorted(str(r.get("cntr_tm")) for r in rows_of(d_old))
        print(f"   [시작일자={old}(6일 전)] {len(r_old)}봉 {r_old[0] if r_old else '-'} ~ {r_old[-1] if r_old else '-'}")
        d = k.request(TR_MINUTE_CHART, PATH_CHART, {**body, "strt_dt": datetime.now(ET).strftime("%Y%m%d")})
        rows = rows_of(d)
        print(f"   [시작일자=오늘] 아래는 이 응답 기준입니다. (두 응답의 범위를 비교하면 시작일자의 뜻을 알 수 있습니다)")
        if len(rows) >= 2:
            asc = str(rows[0].get("cntr_tm")) < str(rows[-1].get("cntr_tm"))
            print(f"   정렬: {'과거→최신' if asc else '최신→과거'}")
        print(f"   1회 응답 {len(rows)}봉 · 연속조회 cont-yn={d.get('_cont_yn')} · 필드 {list(rows[0].keys()) if rows else '-'}")
        if not rows:
            return
        tms = sorted(str(r.get("cntr_tm")) for r in rows)
        print(f"   가장 과거 {tms[0]} ~ 가장 최근 {tms[-1]} (응답 첫 행 {rows[0].get('cntr_tm')})")
        diff = sum(1 for r in rows if str(r.get("cntr_tm"))[:8] != str(r.get("bus_dt")))
        hours = collections.Counter(str(r.get("cntr_tm"))[8:10] for r in rows)
        print(f"   시(hour) 분포: {dict(sorted(hours.items()))}")
        tz = "KST" if diff > max(3, len(rows) * 0.05) else "ET"
        print(f"   영업일자와 날짜가 다른 봉 {diff}개 → 분봉 시간대 추정: {tz}")
        advice["KIWOOM.CHART_TZ"] = tz
        newest = parse_ts14(tms[-1]).replace(tzinfo=ET if tz == "ET" else KST)
        late = sum(1 for t in tms if t[8:10] >= "24")
        if late:
            print(f"   ℹ️ 24시 이상 표기 봉 {late}개 (자정을 넘긴 주간거래를 영업일자에 붙여 적는 키움 방식) → 봇이 실제 시각으로 변환합니다")
        age = (datetime.now(ET) - newest.astimezone(ET)).total_seconds() / 60
        print(f"   최신 봉은 지금보다 {age:+.1f}분 전 (장중인데 2분 넘게 차이나면 시세 지연 또는 시간대 추정 오류)")
        if -1.5 < age < -0.2:
            print("   ⚠️ 최신 봉 시각이 미래입니다 → 봉 시각이 '끝 시각' 표기일 수 있음: KIWOOM.CANDLE_TIME_IS_END=true 검토")
            advice["KIWOOM.CANDLE_TIME_IS_END"] = True
        pre = sum(v for h, v in hours.items() if (tz == "ET" and "04" <= h < "09"))
        print(f"   프리장(04~09시 ET) 봉 {pre}개 " + ("✅ 포함" if pre else "⚠️ 없음 — 프리장 시간에 다시 실행해 확인하세요. 프리장 봉이 안 오면 프리장 매매 판단이 어렵습니다."))
    show("5. 1분봉 (usa06011) — 가장 중요", _minute)

    show("6. 일봉 (usa06012)", lambda: print("   최근 3일:", [(r.get("dt"), r.get("cur_prc")) for r in k.daily_chart(
        "SOXL", datetime.now(ET).strftime("%Y%m%d"))[:3]], "← 어제·그제 날짜면 정상"))
    show("7. 달러 예수금 (ust21110)", lambda: print("  ", {a: b for a, b in k.deposit_usd().items() if a != "raw"}))

    def _hold():
        h = k.holdings()
        print(f"   보유 {len(h['items'])}종목:", {s: (i["qty"], i["avg_price"]) for s, i in h["items"].items()})
    show("8. 보유 종목 (ust21070)", _hold)

    def _orders():
        d = k.request(TR_ORDERS_TODAY, PATH_ACCOUNT, {"slby_tp": "0", "stex_tp": "", "stk_cd": ""})
        rows = rows_of(d)
        print(f"   당일 주문 {len(rows)}행 · 목록 키 {[x for x, v in d.items() if isinstance(v, list)]}")
        if rows:
            print("   첫 행:", json.dumps(rows[0], ensure_ascii=False)[:600])
            need = ("ord_no", "ord_qty", "cntr_qty", "cntr_uv", "ord_remnq", "slby_tp")
            miss = [f for f in need if f not in rows[0]]
            print("   ✅ 필수 필드 모두 있음" if not miss else f"   ⚠️ 없는 필드: {miss} → toss_bridge._summarize 의 필드명 보정 필요")
        else:
            print("   (주문 기록이 없어 필드를 확인하지 못했습니다. --order-test 로 확인할 수 있습니다)")
    show("9. 당일 주문체결 확인 (ust21510)", _orders)
    show("10. 환율 (ust31301)", lambda: print("   USD/KRW", k.fx_rate()))

    if "--order-test" in sys.argv and q and q["price"] > 0:
        def _order_test():
            px = max(1.0, round(q["price"] * 0.5, 2))
            print(f"   SOXL 1주 지정가 ${px:.2f} 매수 주문 (현재가 ${q['price']:.2f} 의 절반 — 체결되지 않습니다)")
            r = k.buy("SOXL", 1, f"{px:.2f}")
            print("   주문 응답:", r["ord_no"], r["msg"])
            rows = [x for x in k.orders_today("SOXL") if str(x.get("ord_no")) == r["ord_no"]]
            print("   당일 주문조회 행:", json.dumps(rows[:1], ensure_ascii=False)[:500])
            print("   미체결 조회:", [x.get("ord_no") for x in k.open_orders("SOXL")])
            c = k.cancel("SOXL", r["ord_no"])
            print("   취소 응답:", c["ord_no"], c["msg"])
            rows = [x for x in k.orders_today("SOXL") if str(x.get("ord_no")) in (r["ord_no"], c["ord_no"])
                    or str(x.get("orig_ord_no")) == r["ord_no"]]
            for x in rows:
                print("   취소 후 행:", {f: x.get(f) for f in ("ord_cntr_tp", "ord_no", "orig_ord_no", "ord_qty", "cntr_qty",
                                                          "cncl_qty", "ord_remnq", "ord_stat")})
            print(f"   지금 시간대: {'프리장' if 4 <= datetime.now(ET).hour < 9 else '그 외'} — 프리장(한국 18:00~23:30, 서머타임 17:00~22:30)에 "
                  "이 테스트가 성공해야 프리장 자동매매가 가능합니다.")
        show("11. 주문 → 취소 시험", _order_test)

    print("\n══ 권장 설정 (config.json) " + "═" * 30)
    for a, b in advice.items():
        print(f"   {a} = {b}   (텔레그램: /set {a} {b})")
    print("   FEE.US_COMMISSION_RATE = 본인 계좌의 실제 편도 수수료율 (예: 0.25% → 0.0025)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
