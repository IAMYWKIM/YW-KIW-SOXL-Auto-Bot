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
TR_STEX_LOOKUP = "usa10098"      # 미국주식 거래소구분 조회 (종목코드만으로 조회)
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
STEX_CANDIDATES = ("NY", "NA", "ND")   # NA: AMEX, ND: NASDAQ, NY: NYSE
# 키움 명세 예시에서 확인된 값: SOXL → NY (NYSE Arca 상장 ETF 가 NY 로 분류됨). SGOV 도 NYSE Arca, QQQ 는 NASDAQ.
KNOWN_STEX = {"SOXL": "NY", "SGOV": "NY", "QQQ": "ND"}

# 키움 오류코드 → 원인 안내 (키움 공식 명세 error_codes)
ERROR_HINTS = {
    "8001": "앱키/시크릿키가 틀렸습니다 (.env 확인)",
    "8002": "앱키/시크릿키가 틀렸습니다 (.env 확인)",
    "8010": "토큰을 받은 IP와 요청 IP가 다릅니다 — 서버 IP가 바뀌었는지 확인",
    "8030": "실전/모의 구분이 다른 앱키입니다 — TRADE_MODE 와 키(_MOCK 여부)를 맞추세요",
    "8031": "실전/모의 구분이 다른 토큰입니다 — TRADE_MODE 와 키를 맞추세요",
    "8040": "단말기(IP) 인증 실패 — 키움 OpenAPI 포털에 이 서버의 공인 IP를 등록하세요",
    "8050": "지정단말기 인증 실패 — 키움 OpenAPI 포털에 이 서버의 공인 IP를 등록하세요",
    "8103": "토큰/단말기 인증 실패 — 서버 공인 IP 등록과 앱키를 확인하세요",
    "8104": "모의투자에서 지원하지 않는 API 입니다 — 해외주식 모의투자 신청 여부를 확인하거나 DRY_RUN 으로 시험하세요",
    "8200": "법인 계좌는 이 API를 쓸 수 없습니다",
    "7": "키움 서버 일시 오류입니다 — 봇이 자동 재시도하고 직전 값으로 이어 갑니다 (계속되면 키움 점검 시간 여부 확인)",
    "20": "키움 내부 중계 서버 일시 실패(-994 fail to sendReceive) — 봇이 자동 재시도합니다. 같은 오류가 몇 분 이상 계속되면 키움 점검·장애 여부 확인",
    "1700": "호출 한도 초과 — KIWOOM.QUERY_MIN_INTERVAL 을 0.34 로",
    "1903": "종목코드/거래소구분이 맞지 않습니다 — config.json SYMBOLS 확인",
    "1504": "이 주소에서 지원하지 않는 API ID 입니다",
    "1505": "존재하지 않는 API ID 입니다 (계정에 해외주식 API 사용 신청이 안 되어 있을 수 있음)",
}


TRANSIENT_CODES = {"7", "20", "1999", "1687"}   # 20: 실측(2026-11-18) [2000](-994:fail to sendReceive) 키움 내부 중계 실패
TRANSIENT_WORDS = ("처리하는 중에 오류", "[1631]", "예기치 못한", "일시적", "잠시 후", "fail to sendReceive", "(-994")


def is_transient(code, msg: str = "", http_status: int = 200) -> bool:
    """키움 서버 쪽 일시 오류인지 (재시도·직전값 사용 대상). 실측: return_code=7 '서비스를 처리하는 중에 오류가 발생했습니다[1631]'"""
    return (str(code) in TRANSIENT_CODES or int(http_status or 200) >= 500
            or any(w in str(msg) for w in TRANSIENT_WORDS) or str(code) in ("NET", "TIMEOUT"))


def hint_for(code, msg: str = "") -> str:
    c = str(code or "")
    if c in ERROR_HINTS:
        return ERROR_HINTS[c]
    for k, v in ERROR_HINTS.items():
        if f"[{k}" in str(msg) or f"{k}:" in str(msg):
            return v
    return ""


class KiwoomApiError(RuntimeError):
    def __init__(self, api_id: str, code, msg: str, http_status: int = 200):
        self.api_id, self.code, self.msg, self.http_status = api_id, code, str(msg or ""), http_status
        self.hint = hint_for(code, self.msg)
        self.transient = is_transient(code, self.msg, http_status)
        super().__init__(f"{api_id} 오류 return_code={code} http={http_status} msg={self.msg}"
                         + (f" → {self.hint}" if self.hint else ""))


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


def parse_ts14(s: str):
    """키움 시각 'YYYYMMDDHHMMSS' → datetime (시간대 없음).
    키움은 자정을 넘긴 주간거래(데이마켓) 체결을 '영업일자 + 24시 이상'으로 적습니다
    (실측: 2026-11-11 02:33 ET 의 봉 = '20261110263300'). 24 이상이면 다음 날로 넘깁니다."""
    import re as _re
    t = _re.sub(r"\D", "", str(s or ""))
    if len(t) < 12:
        return None
    t = (t + "00")[:14]
    try:
        base = datetime.strptime(t[:8], "%Y%m%d")
        hh, mm, ss = int(t[8:10]), int(t[10:12]), int(t[12:14])
    except ValueError:
        return None
    if mm > 59 or ss > 59 or hh > 47:
        return None
    return base + timedelta(hours=hh, minutes=mm, seconds=ss)


def split_hms(s: str):
    """'HHMMSS'(HH 는 24 이상 가능) → (추가일수, 시, 분, 초). 형식이 이상하면 None"""
    import re as _re
    t = _re.sub(r"\D", "", str(s or ""))
    if len(t) < 6:
        return None
    hh, mm, ss = int(t[0:2]), int(t[2:4]), int(t[4:6])
    if mm > 59 or ss > 59 or hh > 47:
        return None
    return hh // 24, hh % 24, mm, ss


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
        self._rejected_token = None
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
    # ── 토큰 공유 파일 (v1.0.10) ─────────────────────────────
    # 같은 앱키로 토큰을 새로 받으면 앞서 받은 토큰이 무효(8005)가 될 수 있어,
    # 봇·diag·기록 스크립트 등 이 폴더의 모든 프로세스가 토큰 하나를 파일로 함께 씁니다.
    def _token_file(self):
        import hashlib
        h = hashlib.sha256(f"{self.base}|{self.app_key}".encode()).hexdigest()[:12]
        return config.DATA_DIR / f"kiwoom_token_{h}.json"

    def _load_shared_token(self, reject: str = ""):
        try:
            import json as _j
            d = _j.loads(self._token_file().read_text(encoding="utf-8"))
            tok, exp = str(d.get("token") or ""), datetime.strptime(d.get("expire", ""), "%Y%m%d%H%M%S")
            if tok and tok != reject and exp > datetime.now():
                return tok, exp
        except Exception:
            pass
        return None, None

    def _save_shared_token(self):
        try:
            import json as _j, os as _os
            f = self._token_file()
            f.parent.mkdir(parents=True, exist_ok=True)
            tmp = f.with_suffix(".tmp")
            tmp.write_text(_j.dumps({"token": self._token, "expire": self._token_expire.strftime("%Y%m%d%H%M%S"),
                                     "pid": _os.getpid()}), encoding="utf-8")
            _os.chmod(tmp, 0o600)
            _os.replace(tmp, f)
        except Exception as e:
            log.debug("토큰 공유 파일 저장 실패: %s", e)

    def _ensure_token(self) -> str:
        with self._lock:
            if self._token and datetime.now() < self._token_expire:
                return self._token
            if not self.app_key or not self.secret_key:
                raise KiwoomApiError("au10001", "NO_KEY", "키움 앱키/시크릿키가 비어 있습니다 (.env 확인)")
            tok, exp = self._load_shared_token(reject=self._rejected_token or "")
            if tok:                                   # 다른 프로세스가 받아 둔 유효한 토큰을 그대로 사용
                self._token, self._token_expire = tok, exp
                log.info("공유 토큰 사용 (만료 %s)", exp.strftime("%m-%d %H:%M"))
                return tok
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
            self._rejected_token = None
            self._save_shared_token()
            return self._token

    def invalidate_token(self):
        with self._lock:
            self._rejected_token = self._token     # 방금 거부된 토큰은 공유 파일에 있어도 다시 쓰지 않음
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
            token_retried = False
            max_try = 1 if is_order else 3          # 주문은 중복 접수 위험이 있어 재시도하지 않습니다
            for attempt in range(1, max_try + 2):
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
                except (requests.Timeout, requests.ConnectionError) as ne:
                    if is_order or attempt >= max_try:
                        self.err_by_api[api_id] += 1
                        raise KiwoomApiError(api_id, "TIMEOUT" if isinstance(ne, requests.Timeout) else "NET",
                                             f"키움 서버 연결 실패: {type(ne).__name__}")
                    time.sleep(0.6 * attempt)
                    continue
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
                # 토큰 무효: HTTP 401, return_code 8005, 또는 메시지 안의 [8005:...] → 1회 재발급 후 재시도
                # (실측 2026-11-18: return_code=3, "인증에 실패했습니다[8005:Token이 유효하지 않습니다]".
                #  v1.0.9 까지는 메시지 앞 12글자만 봐서 이 형태를 놓쳐 재발급하지 못했음)
                if not token_retried and (status == 401 or str(rc) == "8005" or "8005" in msg
                                          or ("인증에 실패" in msg and "Token" in msg)):
                    token_retried = True
                    self.invalidate_token()
                    continue
                if not is_order and attempt < max_try and is_transient(rc, msg, status):
                    log.info("키움 일시 오류 재시도 %s (%d/%d): %s %s", api_id, attempt, max_try - 1, rc, msg[:60])
                    time.sleep(0.6 * attempt)
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
        if sym in KNOWN_STEX:                     # AUTO 여도 명세로 확인된 값이 있으면 그대로 사용
            self._stex_cache[sym] = KNOWN_STEX[sym]
            return KNOWN_STEX[sym]
        errors = []
        try:
            d = self.request(TR_STEX_LOOKUP, PATH_STKINFO, {"stk_cd": sym})
            got = str(pick(d, "stex_tp", default="") or "").upper()
            if not got:
                lst = rows_of(d, "list")
                got = str(lst[0].get("stex_tp") or "").upper() if lst else ""
            if got in STEX_CANDIDATES:
                self._stex_cache[sym] = got
                log.info("거래소구분 확인(usa10098): %s → %s", sym, got)
                return got
        except KiwoomApiError as e:
            if e.api_id == "au10001" or str(e.code).startswith("8"):
                raise                             # 인증/권한 오류는 그대로 올려 진짜 원인을 보여 줍니다
            errors.append(f"usa10098: {e.code} {e.msg}")
        for cand in STEX_CANDIDATES:
            try:
                d = self.request(TR_STOCK_LOOKUP, PATH_STKINFO, {"stex_tp": cand, "stk_cd": sym})
                if str(pick(d, "stk_cd", default="")).upper() == sym or pick(d, "stk_nm"):
                    self._stex_cache[sym] = cand
                    log.info("거래소구분 탐색 결과: %s → %s (%s)", sym, cand, pick(d, "mkgb", default=""))
                    return cand
                errors.append(f"usa10100({cand}): 빈 응답")
            except KiwoomApiError as e:
                if e.api_id == "au10001" or str(e.code).startswith("8"):
                    raise
                errors.append(f"usa10100({cand}): {e.code} {e.msg}")
        raise KiwoomApiError(TR_STOCK_LOOKUP, "NO_STEX",
                             f"{sym} 거래소구분을 찾지 못했습니다 (config.json SYMBOLS.{sym} 에 NY/ND/NA 지정). "
                             f"시도 결과: {' | '.join(errors)[:400]}")

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
