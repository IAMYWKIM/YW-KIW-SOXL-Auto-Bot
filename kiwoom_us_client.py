# -*- coding: utf-8 -*-
"""
kiw_soxl_trader - ★ 키움 블록 A : 키움증권 REST API (미국주식) 클라이언트

이 파일은 "키움 서버와 실제로 통신하는 유일한 곳" 입니다.
  - 인증: POST {base}/oauth2/token (client_credentials)  — kiw_scalp_trader 와 동일
  - TR  : POST {base}{path}, 헤더 api-id 로 TR 구분, cont-yn/next-key 연속조회
  - 미국주식 TR 은 /api/us/* 경로, TR ID 는 usa*(시세·차트) / ust*(주문·계좌)

TR ID·필드명 출처: 키움증권 공식 저장소 Kiwoom-Securities/Kiwoom-REST-API
(kiwoom/_data/kiwoom_api_spec.json, 2026-09-15 커밋 기준).
※ 실제 응답이 명세와 다르면 이 파일 상단 상수와 각 메서드의 pick(...) 후보만 고치면 됩니다.
   `python3 diag.py` 가 실제 응답을 찍어 주므로 그것을 보고 맞추세요.

이 파일은 원본(토스) 엔진을 전혀 알지 못합니다. 토스 형식으로의 번역은 toss_bridge.py 가 합니다.
"""
import collections
import threading
import time
from datetime import datetime, timedelta

import requests

import config
from logger import get_logger

log = get_logger("kiwoom")

# ── TR ID (응답 오류 시 여기만 수정) ───────────────────────────────
TR_STOCK_LOOKUP = "usa10100"     # 미국주식 종목 조회 (거래소구분 확인)
TR_QUOTE = "usa20100"            # 미국주식 현재가 종목정보
TR_ORDERBOOK = "usa20101"        # 미국주식 현재가 10호가
TR_TICKS = "usa20150"            # 미국주식 상세 체결내역
TR_MINUTE_CHART = "usa06011"     # 미국주식 분 차트
TR_DAILY_CHART = "usa06012"      # 미국주식 일 차트
TR_ORDER_BUY = "ust20000"        # 미국주식 매수 주문
TR_ORDER_SELL = "ust20001"       # 미국주식 매도 주문
TR_ORDER_MODIFY = "ust20002"     # 미국주식 정정 주문
TR_ORDER_CANCEL = "ust20003"     # 미국주식 취소 주문
TR_ORDERABLE = "ust31490"        # 미국주식 주문가능수량(종목/증거금률별)
TR_UNFILLED = "ust21050"         # 미국주식 원장 미체결
TR_BALANCE = "ust21070"          # 미국주식 원장잔고확인 (보유)
TR_DEPOSIT = "ust21110"          # 해외주식 예수금 (통화별)
TR_ORDERS_DAILY = "ust21150"     # 미국주식 일별 주문체결내역
TR_ORDERS_TODAY = "ust21510"     # 미국주식 당일 주문체결 확인
TR_REALIZED_TODAY = "ust21630"   # 미국주식 당일 실현손익
TR_FX_RATE = "ust31301"          # 환율 조회

PATH_STKINFO = "/api/us/stkinfo"
PATH_MRKCOND = "/api/us/mrkcond"
PATH_CHART = "/api/us/chart"
PATH_ORDER = "/api/us/ordr"
PATH_ACCOUNT = "/api/us/acnt"
PATH_EXCHANGE = "/api/us/exchange"

TRDE_TP_LIMIT = "00"             # 해외매매구분: 00 지정가 (이 봇은 지정가만 사용)
STEX_CANDIDATES = ("NA", "ND", "NY")   # NA: AMEX, ND: NASDAQ, NY: NYSE


class KiwoomApiError(RuntimeError):
    def __init__(self, api_id: str, code, msg: str, http_status: int = 200):
        self.api_id, self.code, self.msg, self.http_status = api_id, code, str(msg or ""), http_status
        super().__init__(f"{api_id} 오류 return_code={code} http={http_status} msg={self.msg}")


def fnum(v, default=0.0) -> float:
    """키움 숫자 문자열('+201.4700' '000000000395' '-00000002438684' '1,537.20' '') → float (부호 유지)"""
    if v is None:
        return default
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).replace(",", "").strip()
    if not s or s in ("-", "+"):
        return default
    neg = s.startswith("-")
    s = s.lstrip("+-")
    try:
        f = float(s)
    except ValueError:
        return default
    return -f if neg else f


def anum(v, default=0.0) -> float:
    """부호 무시 절대값 (가격 필드에 전일대비 부호가 붙어 나옵니다)"""
    return abs(fnum(v, default))


def pick(d: dict, *keys, default=None):
    """응답 dict 에서 존재하는 첫 키의 값 (필드명 편차 방어)"""
    if isinstance(d, dict):
        for k in keys:
            if k in d and d[k] not in (None, ""):
                return d[k]
    return default


def rows_of(data: dict, *list_keys) -> list:
    """응답에서 목록 추출 (지정 키 → 첫 list 값. 명세 예시의 'result_lsit' 오타 같은 편차 방어)"""
    if not isinstance(data, dict):
        return []
    for k in list_keys + ("result_list", "result_lsit"):
        v = data.get(k)
        if isinstance(v, list):
            return [r for r in v if isinstance(r, dict)]
    for k, v in data.items():
        if isinstance(v, list) and not str(k).startswith("_"):
            return [r for r in v if isinstance(r, dict)]
    return []


class KiwoomUsClient:
    """동기(requests) 클라이언트. 여러 스레드에서 불러도 안전하도록 호출을 직렬화합니다."""

    def __init__(self, base_url: str = None, app_key: str = None, secret_key: str = None):
        self.base = (base_url or config.KIWOOM_API_BASE).rstrip("/")
        self.app_key = app_key if app_key is not None else config.KIWOOM_APP_KEY
        self.secret_key = secret_key if secret_key is not None else config.KIWOOM_SECRET_KEY
        self._session = requests.Session()
        self._token = None
        self._token_expire = datetime.min
        self._lock = threading.RLock()
        self._last_query = 0.0
        self._last_order = 0.0
        self._stex_cache = {}
        # 부하 계측 (/load)
        self._calls = collections.deque(maxlen=6000)
        self.by_api = collections.Counter()
        self.err_by_api = collections.Counter()
        self.peak_per_min = 0
        self._stat_day = ""

    # ── 인증 ───────────────────────────────────────────────────
    def _ensure_token(self) -> str:
        with self._lock:
            if self._token and datetime.now() < self._token_expire:
                return self._token
            if not self.app_key or not self.secret_key:
                raise KiwoomApiError("au10001", "NO_KEY", "키움 앱키/시크릿키가 비어 있습니다 (.env 확인)")
            r = self._session.post(
                f"{self.base}/oauth2/token",
                json={"grant_type": "client_credentials", "appkey": self.app_key, "secretkey": self.secret_key},
                headers={"Content-Type": "application/json;charset=UTF-8"}, timeout=10)
            try:
                data = r.json()
            except ValueError:
                raise KiwoomApiError("au10001", "BAD_JSON", r.text[:200], r.status_code)
            token = pick(data, "token", "access_token")
            if not token:
                raise KiwoomApiError("au10001", data.get("return_code"), data.get("return_msg") or str(data)[:200],
                                     r.status_code)
            self._token = token
            exp = None
            raw_exp = str(pick(data, "expires_dt", default="") or "")
            if len(raw_exp) >= 14 and raw_exp[:14].isdigit():
                try:
                    exp = datetime.strptime(raw_exp[:14], "%Y%m%d%H%M%S") - timedelta(minutes=10)
                except ValueError:
                    exp = None
            self._token_expire = exp if exp and exp > datetime.now() else datetime.now() + timedelta(hours=6)
            log.info("액세스 토큰 발급 완료 (mock=%s, 만료 %s)", config.KIWOOM_IS_MOCK,
                     self._token_expire.strftime("%m-%d %H:%M"))
            return self._token

    def invalidate_token(self):
        with self._lock:
            self._token = None

    # ── 공통 요청 ─────────────────────────────────────────────
    def request(self, api_id: str, path: str, body: dict, cont_yn: str = "", next_key: str = "",
                is_order: bool = False) -> dict:
        t_req = time.time()
        with self._lock:
            kcfg = config.get("KIWOOM", {}) or {}
            if is_order:
                gap_need, last = float(kcfg.get("ORDER_MIN_INTERVAL", 0.12)), self._last_order
            else:
                gap_need, last = float(kcfg.get("QUERY_MIN_INTERVAL", 0.22)), self._last_query
            gap = time.time() - last
            if gap < gap_need:
                time.sleep(gap_need - gap)
            t_start = time.time()
            timeout = float(kcfg.get("TIMEOUT_SEC", 8))
            data, status = {}, 0
            for attempt in (1, 2):
                headers = {
                    "Content-Type": "application/json;charset=UTF-8",
                    "authorization": f"Bearer {self._ensure_token()}",
                    "api-id": api_id,
                }
                if cont_yn:
                    headers["cont-yn"] = cont_yn
                if next_key:
                    headers["next-key"] = next_key
                try:
                    r = self._session.post(f"{self.base}{path}", headers=headers, json=body or {}, timeout=timeout)
                finally:
                    now = time.time()
                    if is_order:
                        self._last_order = now
                    else:
                        self._last_query = now
                status = r.status_code
                try:
                    data = r.json()
                except ValueError:
                    data = {"return_code": -1, "return_msg": f"JSON 아님: {r.text[:120]}"}
                rc = data.get("return_code")
                msg = str(data.get("return_msg") or "")
                # 토큰 만료: HTTP 401 또는 return_code 8005 / 메시지 [8005:...] → 1회 재발급 후 재시도
                if attempt == 1 and (status == 401 or str(rc) == "8005" or "8005" in msg[:12]):
                    self.invalidate_token()
                    continue
                break
            self._record(api_id, t_req, t_start, time.time())
            rc = data.get("return_code")
            if status >= 400 or rc not in (None, 0, "0"):
                self.err_by_api[api_id] += 1
                raise KiwoomApiError(api_id, rc, data.get("return_msg"), status)
            data["_next_key"] = r.headers.get("next-key", "") or ""
            data["_cont_yn"] = r.headers.get("cont-yn", "N") or "N"
            return data

    def _record(self, api_id, t_req, t_start, t_end):
        day = datetime.now().strftime("%Y%m%d")
        if day != self._stat_day:
            self._stat_day, self.peak_per_min = day, 0
            self.by_api.clear()
            self.err_by_api.clear()
            self._calls.clear()
        self._calls.append((t_end, t_start - t_req, t_end - t_start))
        self.by_api[api_id] += 1
        n60 = sum(1 for c in self._calls if c[0] >= t_end - 60)
        self.peak_per_min = max(self.peak_per_min, n60)

    def load_stats(self) -> dict:
        now = time.time()
        last = [c for c in self._calls if c[0] >= now - 60]
        last5 = [c for c in self._calls if c[0] >= now - 300]
        qmin = float(config.get("KIWOOM.QUERY_MIN_INTERVAL", 0.22) or 0.22)
        return {"per_min": len(last), "per_min_5": len(last5) / 5, "peak_per_min": self.peak_per_min,
                "avg_wait": (sum(c[1] for c in last5) / len(last5)) if last5 else 0.0,
                "avg_latency": (sum(c[2] for c in last5) / len(last5)) if last5 else 0.0,
                "busy_pct": min(100.0, sum(c[1] + c[2] for c in last) / 60 * 100),
                "today": sum(self.by_api.values()), "errors": sum(self.err_by_api.values()),
                "top": self.by_api.most_common(8), "max_per_min": int(60 / max(qmin, 0.05))}

    # ── 종목 → 거래소구분 ─────────────────────────────────────
    def stex(self, symbol: str) -> str:
        sym = symbol.upper().strip()
        if sym in self._stex_cache:
            return self._stex_cache[sym]
        fixed = str(config.get(f"SYMBOLS.{sym}", "AUTO") or "AUTO").upper()
        if fixed in STEX_CANDIDATES:
            self._stex_cache[sym] = fixed
            return fixed
        try:
            d = self.request(TR_STOCK_LOOKUP, PATH_STKINFO, {"stex_tp": "", "stk_cd": sym})
            got = str(pick(d, "stex_tp", default="") or "").upper()
            if got in STEX_CANDIDATES:
                self._stex_cache[sym] = got
                log.info("거래소구분 확인: %s → %s (%s)", sym, got, pick(d, "mkgb", default=""))
                return got
        except Exception as e:
            log.debug("종목 조회(usa10100) 실패, 시세 조회로 거래소구분 탐색: %s", e)
        for cand in STEX_CANDIDATES:
            try:
                d = self.request(TR_QUOTE, PATH_MRKCOND, {"stex_tp": cand, "stk_cd": sym})
                if anum(pick(d, "cur_prc", "base_close_pric")) > 0:
                    self._stex_cache[sym] = cand
                    log.info("거래소구분 탐색 결과: %s → %s", sym, cand)
                    return cand
            except Exception:
                continue
        raise KiwoomApiError(TR_STOCK_LOOKUP, "NO_STEX", f"{sym} 의 거래소구분을 찾지 못했습니다 (config.json SYMBOLS 에 직접 지정)")

    # ── 시세 ───────────────────────────────────────────────────
    def quote(self, symbol: str) -> dict:
        d = self.request(TR_QUOTE, PATH_MRKCOND, {"stex_tp": self.stex(symbol), "stk_cd": symbol.upper()})
        return {"symbol": symbol.upper(), "price": anum(pick(d, "cur_prc")),
                "prev_close": anum(pick(d, "base_close_pric")), "open": anum(pick(d, "open_pric")),
                "high": anum(pick(d, "high_pric")), "low": anum(pick(d, "low_pric")),
                "change_pct": fnum(pick(d, "flu_rt")), "volume": anum(pick(d, "acc_trde_qty")),
                "fx": anum(pick(d, "base_exrt")), "halted": str(pick(d, "trd_susp_tp", default="0")) not in ("0", ""),
                "name": pick(d, "stk_nm", default=""), "raw": d}

    def orderbook(self, symbol: str) -> dict:
        d = self.request(TR_ORDERBOOK, PATH_MRKCOND, {"stex_tp": self.stex(symbol), "stk_cd": symbol.upper()})
        asks, bids = [], []
        for i in range(1, 11):
            ap, bp = anum(d.get(f"sel_{i}bid")), anum(d.get(f"buy_{i}bid"))
            if ap > 0:
                asks.append({"price": ap, "volume": anum(d.get(f"sel_{i}bid_req"))})
            if bp > 0:
                bids.append({"price": bp, "volume": anum(d.get(f"buy_{i}bid_req"))})
        if not asks and anum(d.get("fpr_sel_bid")) > 0:
            asks.append({"price": anum(d.get("fpr_sel_bid")), "volume": 0})
        if not bids and anum(d.get("fpr_buy_bid")) > 0:
            bids.append({"price": anum(d.get("fpr_buy_bid")), "volume": 0})
        asks.sort(key=lambda x: x["price"])
        bids.sort(key=lambda x: -x["price"])
        return {"asks": asks, "bids": bids, "price": anum(d.get("cur_prc")),
                "date": str(d.get("dt") or ""), "time": str(d.get("bid_tm") or ""), "raw": d}

    def ticks(self, symbol: str) -> list:
        d = self.request(TR_TICKS, PATH_MRKCOND, {"stex_tp": self.stex(symbol), "stk_cd": symbol.upper()})
        return [{"price": anum(r.get("cur_prc")), "volume": anum(r.get("trde_qty")), "tm": str(r.get("cntr_tm") or "")}
                for r in rows_of(d)]

    def minute_chart(self, symbol: str, start_date: str, max_pages: int = 1, tic_scope: str = "1",
                     stop_before: str = "") -> list:
        """1분봉 원본 행(최신→과거 순으로 들어오는 것으로 가정하지 않고 그대로 모아 반환).
        stop_before: 이 시각(YYYYMMDDHHMMSS)보다 과거 봉이 나오면 연속조회 중단."""
        body = {"stex_tp": self.stex(symbol), "stk_cd": symbol.upper(), "strt_dt": start_date,
                "tic_scope": tic_scope, "upd_stkpc_tp": "1", "exrt_appl_tp": "0"}
        out, cont, nk = [], "", ""
        for _ in range(max(1, max_pages)):
            d = self.request(TR_MINUTE_CHART, PATH_CHART, body, cont_yn=cont, next_key=nk)
            page = rows_of(d)
            out.extend(page)
            if d.get("_cont_yn") != "Y" or not d.get("_next_key") or not page:
                break
            if stop_before and min(str(r.get("cntr_tm") or "9") for r in page) < stop_before:
                break
            cont, nk = "Y", d["_next_key"]
        return out

    def daily_chart(self, symbol: str, start_date: str) -> list:
        d = self.request(TR_DAILY_CHART, PATH_CHART, {
            "stex_tp": self.stex(symbol), "stk_cd": symbol.upper(), "strt_dt": start_date,
            "upd_stkpc_tp": "1", "exrt_appl_tp": "0"})
        return rows_of(d)

    def fx_rate(self) -> float:
        d = self.request(TR_FX_RATE, PATH_EXCHANGE, {"exch_tp": "1"})
        return anum(pick(d, "buy_aplc_exrt", "aplc_exrt", "spcl_bf_exrt"))

    # ── 계좌 ───────────────────────────────────────────────────
    def holdings(self) -> dict:
        """보유 종목. 반환: {"summary": {...}, "items": {SYM: {...}}}"""
        d = self.request(TR_BALANCE, PATH_ACCOUNT, {"stex_tp": "", "stk_cd": ""})
        items = {}
        for r in rows_of(d):
            sym = str(r.get("stk_cd") or "").upper().strip()
            qty = int(anum(pick(r, "poss_qty", "qty")))
            if not sym or qty <= 0:
                continue
            avg = anum(pick(r, "frgn_stk_book_uv", "prch_uv"))
            last = anum(pick(r, "now_pric")) or avg
            items[sym] = {"symbol": sym, "qty": qty, "sellable": int(anum(pick(r, "sell_alowq", default=qty))),
                          "avg_price": avg, "last_price": last,
                          "purchase_amount": anum(pick(r, "frgn_stk_book_amt", "prch_amt")) or qty * avg,
                          "eval_amount": anum(pick(r, "evlt_amt")) or qty * last,
                          "pnl": fnum(pick(r, "pl_amt", "evlt_pl_amt")), "pnl_pct": fnum(pick(r, "pl_rt", "evlt_pl_rt")),
                          "name": pick(r, "frgn_stk_nm", default=""), "fx": anum(pick(r, "exch_rate"))}
        summary = {"eval_amount": anum(d.get("tot_evlt_amt")), "purchase_amount": anum(d.get("tot_prch_amt")),
                   "pnl": fnum(d.get("tot_pl_amt")), "pnl_pct": fnum(d.get("tot_pl_rt")),
                   "today_realized": fnum(d.get("tdy_pl_amt"))}
        return {"summary": summary, "items": items}

    def deposit_usd(self) -> dict:
        """달러 예수금. 반환: {"cash": 외화예수금, "orderable": 외화주문가능금액, "withdrawable": ...}"""
        d = self.request(TR_DEPOSIT, PATH_ACCOUNT, {})
        for r in rows_of(d):
            if str(r.get("crnc_code") or "").upper() == "USD":
                return {"cash": fnum(r.get("fc_entra")), "orderable": fnum(r.get("fc_ord_alowa")),
                        "withdrawable": fnum(r.get("fc_pymn_alowa")), "raw": r}
        return {"cash": 0.0, "orderable": 0.0, "withdrawable": 0.0, "raw": {}}

    def orderable_usd(self, symbol: str, price: float) -> float:
        d = self.request(TR_ORDERABLE, PATH_ORDER, {"stex_tp": self.stex(symbol), "stk_cd": symbol.upper(),
                                                   "uv": f"{price:.2f}"}, is_order=False)
        return anum(pick(d, "ord_alowa_100", "ord_alowa_50"))

    # ── 주문 조회 (행 원본 반환 — 해석은 toss_bridge 가 담당) ─────
    def orders_today(self, symbol: str = "") -> list:
        body = {"slby_tp": "0", "stex_tp": self.stex(symbol) if symbol else "", "stk_cd": symbol.upper()}
        return self._paged_rows(TR_ORDERS_TODAY, body)

    def orders_on(self, ord_dt: str, symbol: str = "") -> list:
        body = {"ord_dt": ord_dt, "query_tp": "2", "slby_tp": "0",
                "stex_tp": self.stex(symbol) if symbol else "", "stk_cd": symbol.upper(),
                "oppo_trde_tp": "", "fr_ord_no": ""}
        return self._paged_rows(TR_ORDERS_DAILY, body)

    def open_orders(self, symbol: str = "") -> list:
        body = {"ord_dt": "", "slby_tp": "0", "stex_tp": self.stex(symbol) if symbol else "",
                "stk_cd": symbol.upper()}
        return self._paged_rows(TR_UNFILLED, body)

    def _paged_rows(self, api_id: str, body: dict, max_pages: int = 3) -> list:
        out, cont, nk = [], "", ""
        for _ in range(max_pages):
            d = self.request(api_id, PATH_ACCOUNT, body, cont_yn=cont, next_key=nk)
            page = rows_of(d)
            out.extend(page)
            if d.get("_cont_yn") != "Y" or not d.get("_next_key") or not page:
                break
            cont, nk = "Y", d["_next_key"]
        return out

    # ── 주문 (지정가만) ───────────────────────────────────────
    def buy(self, symbol: str, qty: int, price_str: str) -> dict:
        d = self.request(TR_ORDER_BUY, PATH_ORDER, {
            "stex_tp": self.stex(symbol), "stk_cd": symbol.upper(), "ord_qty": str(int(qty)),
            "ord_uv": price_str, "trde_tp": TRDE_TP_LIMIT}, is_order=True)
        return {"ord_no": str(d.get("ord_no") or "").strip(), "msg": d.get("return_msg", ""), "raw": d}

    def sell(self, symbol: str, qty: int, price_str: str) -> dict:
        d = self.request(TR_ORDER_SELL, PATH_ORDER, {
            "stk_cd": symbol.upper(), "stex_tp": self.stex(symbol), "ord_qty": str(int(qty)),
            "ord_uv": price_str, "stop_pric": "", "trde_tp": TRDE_TP_LIMIT}, is_order=True)
        return {"ord_no": str(d.get("ord_no") or "").strip(), "msg": d.get("return_msg", ""), "raw": d}

    def modify(self, symbol: str, orig_ord_no: str, price_str: str) -> dict:
        d = self.request(TR_ORDER_MODIFY, PATH_ORDER, {
            "orig_ord_no": orig_ord_no, "stex_tp": self.stex(symbol), "stk_cd": symbol.upper(),
            "mdfy_uv": price_str, "stop_pric": ""}, is_order=True)
        return {"ord_no": str(d.get("ord_no") or "").strip(), "msg": d.get("return_msg", ""), "raw": d}

    def cancel(self, symbol: str, orig_ord_no: str) -> dict:
        d = self.request(TR_ORDER_CANCEL, PATH_ORDER, {
            "orig_ord_no": orig_ord_no, "stex_tp": self.stex(symbol), "stk_cd": symbol.upper()}, is_order=True)
        return {"ord_no": str(d.get("ord_no") or "").strip(), "msg": d.get("return_msg", ""), "raw": d}
