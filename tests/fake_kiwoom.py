# -*- coding: utf-8 -*-
"""
가짜 키움 REST 서버 (개발·점검용). 키움 공식 명세의 미국주식 TR 응답 모양을 흉내냅니다.
실제 키움 서버의 동작을 보증하지 않습니다 — 브릿지의 번역 로직과 엔진 연결을 확인하는 용도입니다.

  python3 tests/fake_kiwoom.py 18080      # http://127.0.0.1:18080 에서 대기
"""
import json
import sys
import threading
import time
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


class Market:
    def __init__(self):
        self.lock = threading.RLock()
        self.prices = {"SOXL": 30.00, "QQQ": 500.0, "SGOV": 100.5}
        self.cash = 20000.0
        self.pos = {}            # sym → {"qty", "cost"}
        self.orders = []         # dict rows
        self.seq = 280
        self.script = None       # callable(t)->price for SOXL
        self.fee = 0.0025
        self.calls = []
        self.fail = {}           # api_id → 남은 실패 횟수 (일시 오류 재현)

    def price(self, sym):
        if sym == "SOXL" and self.script:
            self.prices["SOXL"] = round(float(self.script(time.time())), 4)
        return self.prices.get(sym, 0.0)

    def match(self):
        with self.lock:
            for o in self.orders:
                if o["remn"] <= 0 or o["kind"] == "12":
                    continue
                px = self.price(o["sym"])
                hit = px <= o["uv"] if o["side"] == "2" else px >= o["uv"]
                if not hit:
                    continue
                q = o["remn"]
                amt = q * o["uv"]
                p = self.pos.setdefault(o["sym"], {"qty": 0, "cost": 0.0})
                if o["side"] == "2":
                    self.cash -= amt * (1 + self.fee)
                    p["qty"] += q
                    p["cost"] += amt
                else:
                    avg = p["cost"] / p["qty"] if p["qty"] else 0
                    self.cash += amt * (1 - self.fee)
                    p["qty"] -= q
                    p["cost"] -= avg * q
                o["cntr"], o["cntr_uv"], o["remn"] = o["qty"], o["uv"], 0
                o["stat"], o["cntr_time"] = "체결", datetime.now(ET).strftime("%H:%M:%S")


MKT = Market()


def _f(v, n=4):
    return f"{v:.{n}f}"


def _q(v):
    return f"{int(v):012d}"


def handle(api_id, body):
    m = MKT
    m.calls.append(api_id)
    if m.fail.get(api_id, 0) > 0:
        m.fail[api_id] -= 1
        return {"return_code": 7, "return_msg": "서비스를 처리하는 중에 오류가 발생했습니다[1631]"}
    ok = {"return_code": 0, "return_msg": "정상적으로 처리되었습니다"}
    sym = str(body.get("stk_cd") or "").upper()
    now = datetime.now(ET)
    if api_id == "usa10100":
        want = "ND" if sym == "QQQ" else "NY"
        if body.get("stex_tp") and body["stex_tp"] != want:
            return {"return_code": 1903, "return_msg": f"종목 정보가 없습니다. 거래소구분={body['stex_tp']}, 종목코드={sym}"}
        return {"stex_tp": want, "stk_cd": sym, "stk_nm": sym, "mkgb": "TEST", **ok}
    if api_id == "usa10098":
        return {"list": [{"stex_tp": "ND" if sym == "QQQ" else "NY", "stk_cd": sym}], **ok}
    if api_id == "usa20100":
        p = m.price(sym)
        return {"stk_cd": sym, "stk_nm": sym, "cur_prc": "+" + _f(p), "base_close_pric": _f(p * 0.99),
                "open_pric": "+" + _f(p), "high_pric": "+" + _f(p * 1.01), "low_pric": "-" + _f(p * 0.98),
                "flu_rt": "+1.01", "acc_trde_qty": "123456", "trd_susp_tp": "0", "base_exrt": "1450.50", **ok}
    if api_id == "usa20101":
        p = m.price(sym)
        d = {"stk_cd": sym, "cur_prc": "+" + _f(p), "dt": now.strftime("%Y%m%d"), "bid_tm": now.strftime("%H:%M"), **ok}
        for i in range(1, 11):
            d[f"sel_{i}bid"] = "+" + _f(p + 0.01 * i)
            d[f"buy_{i}bid"] = "+" + _f(p - 0.01 * i)
            d[f"sel_{i}bid_req"] = "100"
            d[f"buy_{i}bid_req"] = "100"
        return d
    if api_id == "usa20150":
        p = m.price(sym)
        hh = now.hour + 24 if now.hour < 4 else now.hour
        return {"result_list": [{"cur_prc": "+" + _f(p), "trde_qty": "+5", "cntr_tm": f"{hh:02d}" + now.strftime("%M%S")}], **ok}
    if api_id == "usa06011":
        # 실측 형식 재현: 최신→과거, 자정 넘긴 주간거래(00~04시)는 '전 영업일 + 24시 이상'으로 표기
        p = m.price(sym)
        rows = []
        t = now.replace(second=0, microsecond=0)
        for i in range(600):
            tt = t - timedelta(minutes=i)
            if tt.weekday() >= 5 and tt.hour >= 4:
                continue
            pp = p * (1 + 0.0004 * ((i % 17) - 8))
            if tt.hour < 4:
                bd = tt - timedelta(days=1)
                label = bd.strftime("%Y%m%d") + f"{tt.hour + 24:02d}" + tt.strftime("%M%S")
            else:
                bd, label = tt, tt.strftime("%Y%m%d%H%M%S")
            rows.append({"cur_prc": _f(pp), "trde_qty": "1000", "open_pric": _f(pp * 0.999),
                         "high_pric": _f(pp * 1.002), "low_pric": _f(pp * 0.998),
                         "cntr_tm": label, "bus_dt": bd.strftime("%Y%m%d")})
        return {"result_list": rows, **ok}
    if api_id == "usa06012":
        p = m.price(sym)
        rows = []
        d = datetime.strptime(str(body.get("strt_dt") or now.strftime("%Y%m%d")), "%Y%m%d")   # 기준일부터 과거로
        while len(rows) < 30:
            if d.weekday() < 5:
                k = len(rows)
                rows.append({"cur_prc": _f(p * (1 - 0.003 * k)), "open_pric": _f(p * (1 - 0.003 * k) * 0.99),
                             "high_pric": _f(p * (1 - 0.003 * k) * 1.03), "low_pric": _f(p * (1 - 0.003 * k) * 0.97),
                             "acc_trde_qty": "90000000", "dt": d.strftime("%Y%m%d")})
            d -= timedelta(days=1)
        return {"result_list": rows, **ok}
    if api_id == "ust31301":
        return {"buy_aplc_exrt": "1450.50", "aplc_exrt": "1450.500000", **ok}
    m.match()
    with m.lock:
        if api_id == "ust21070":
            rows, te, tp = [], 0.0, 0.0
            for s, p in m.pos.items():
                if p["qty"] <= 0:
                    continue
                last = m.price(s)
                avg = p["cost"] / p["qty"]
                te += p["qty"] * last
                tp += p["cost"]
                rows.append({"stk_cd": s, "frgn_stk_nm": s, "qty": _q(p["qty"]), "poss_qty": _q(p["qty"]),
                             "sell_alowq": _q(p["qty"]), "frgn_stk_book_uv": _f(avg), "now_pric": _f(last),
                             "evlt_amt": _f(p["qty"] * last), "pl_amt": _f(p["qty"] * last - p["cost"]),
                             "pl_rt": _f((last / avg - 1) * 100, 2), "frgn_stk_book_amt": _f(p["cost"]),
                             "exch_rate": "1450.50"})
            return {"crnc_code": "USD", "tot_evlt_amt": _f(te), "tot_prch_amt": _f(tp), "tot_pl_amt": _f(te - tp),
                    "tot_pl_rt": "0.00", "tdy_pl_amt": "0.0000", "result_list": rows, **ok}
        if api_id == "ust21110":
            return {"result_list": [{"crnc_code": "USD", "crnc_nm": "미국달러", "fc_entra": _f(m.cash, 2),
                                     "fc_ord_alowa": _f(max(0, m.cash), 2), "fc_pymn_alowa": _f(m.cash, 2)}], **ok}
        if api_id in ("ust20000", "ust20001"):
            qty, uv = int(body["ord_qty"]), float(body["ord_uv"])
            side = "2" if api_id == "ust20000" else "1"
            if side == "2" and qty * uv > m.cash:
                return {"return_code": 20, "return_msg": "주문가능금액이 부족합니다"}
            if side == "1" and qty > m.pos.get(sym, {}).get("qty", 0):
                return {"return_code": 21, "return_msg": "매도가능수량이 부족합니다"}
            m.seq += 1
            no = f"{m.seq:09d}"
            m.orders.append({"no": no, "orig": "000000000", "kind": "10", "sym": sym, "side": side, "qty": qty,
                             "uv": uv, "cntr": 0, "cntr_uv": 0.0, "remn": qty, "cncl": 0, "stat": "접수",
                             "time": now.strftime("%H:%M:%S"), "cntr_time": ""})
            return {"stk_nm": sym, "ord_no": no, "return_code": 0, "return_msg": "주문입력 완료"}
        if api_id in ("ust20002", "ust20003"):
            orig = next((o for o in m.orders if o["no"] == body["orig_ord_no"]), None)
            if not orig or orig["remn"] <= 0:
                return {"return_code": 30, "return_msg": "정정취소 가능한 원주문이 없습니다"}
            m.seq += 1
            no = f"{m.seq:09d}"
            if api_id == "ust20003":
                orig["cncl"], orig["remn"], orig["stat"] = orig["remn"], 0, "취소"
                m.orders.append({**orig, "no": no, "orig": orig["no"], "kind": "12", "qty": orig["cncl"],
                                 "cntr": 0, "remn": 0, "stat": "취소확인"})
            else:
                rem = orig["remn"]
                orig["remn"], orig["mdfy"], orig["stat"] = 0, rem, "정정"
                m.orders.append({**orig, "no": no, "orig": orig["no"], "kind": "11", "qty": rem, "cntr": 0,
                                 "uv": float(body["mdfy_uv"]), "remn": rem, "stat": "접수", "mdfy": 0})
            return {"stk_nm": sym, "ord_no": no, "return_code": 0, "return_msg": "완료"}
        if api_id in ("ust21510", "ust21150", "ust21050"):
            rows = []
            for o in m.orders:
                if sym and o["sym"] != sym:
                    continue
                if api_id == "ust21050" and (o["remn"] <= 0 or o["kind"] == "12"):
                    continue
                rows.append({"ord_cntr_tp": o["kind"], "ord_no": o["no"], "orig_ord_no": o["orig"], "stk_cd": o["sym"],
                             "frgn_trde_tp": "00", "slby_tp": o["side"], "slby_tp_nm": "매수" if o["side"] == "2" else "매도",
                             "ord_qty": _q(o["qty"]), "ord_uv": _f(o["uv"]), "cntr_qty": _q(o["cntr"]),
                             "cntr_uv": _f(o["cntr_uv"]), "mdfy_qty": _q(o.get("mdfy", 0)), "cncl_qty": _q(o["cncl"]),
                             "ord_remnq": _q(o["remn"]), "ord_time": o["time"], "ord_stat": o["stat"],
                             "ord_stat_nm": o["stat"], "cntr_time": o["cntr_time"]})
            key = "result_lsit" if api_id == "ust21510" else "result_list"   # 명세 예시의 오타까지 재현
            return {key: rows, **ok}
    return {"return_code": 99, "return_msg": f"fake: unknown api {api_id}"}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        if self.path == "/oauth2/token":
            out = {"token": "FAKE", "token_type": "bearer", "return_code": 0,
                   "expires_dt": (datetime.now() + timedelta(hours=12)).strftime("%Y%m%d%H%M%S")}
        else:
            out = handle(self.headers.get("api-id", ""), body)
        raw = json.dumps(out, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json;charset=UTF-8")
        self.send_header("cont-yn", "N")
        self.send_header("next-key", "")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def serve(port=18080):
    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


if __name__ == "__main__":
    serve(int(sys.argv[1]) if len(sys.argv) > 1 else 18080)
    print("fake kiwoom on", sys.argv[1:] or 18080)
    while True:
        time.sleep(3600)
