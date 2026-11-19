# -*- coding: utf-8 -*-
"""
kiw_soxl_trader - ★ 키움 블록 B : 토스 Open API 호환 브릿지 (Toss REST → Kiwoom REST 번역기)

원본 엔진(engine/*.py)은 토스증권 Open API 의 주소(/api/v1/...)와 응답 형식만 알고 있습니다.
이 파일은 그 주소로 들어온 요청을 키움 TR 로 바꿔 보내고, 응답을 토스 형식으로 되돌려 줍니다.
그래서 엔진 소스는 한 글자도 고치지 않고 그대로 둘 수 있고, 원본이 바뀌어도
"엔진이 새로 쓰기 시작한 토스 주소" 가 생겼을 때만 이 파일의 ROUTES 를 보강하면 됩니다.
(engine_sync.py 가 원본 toss_api.py 를 읽어 ROUTES 에 없는 주소를 자동으로 찾아 알려 줍니다.)

토스 주소                         → 키움 TR
  GET  /api/v1/accounts            → (고정 응답: accountSeq=1)
  GET  /api/v1/commissions         → config.json FEE.US_COMMISSION_RATE
  GET  /api/v1/prices              → usa20100 현재가
  GET  /api/v1/trades              → usa20150 상세 체결내역
  GET  /api/v1/orderbook           → usa20101 10호가
  GET  /api/v1/candles             → usa06011 분 차트 / usa06012 일 차트
  GET  /api/v1/exchange-rate       → ust31301 환율 (실패 시 현재가 응답의 환율)
  GET  /api/v1/market-calendar/US  → (미지원: 빈 값 — 엔진은 자체 요일/시간 판정 사용)
  GET  /api/v1/holdings            → ust21070 원장잔고확인
  GET  /api/v1/buying-power        → ust21110 해외주식 예수금(외화주문가능금액)
  GET  /api/v1/sellable-quantity   → ust21070 매도가능수량
  POST /api/v1/orders              → ust20000 매수 / ust20001 매도 (지정가)
  POST /api/v1/orders/{id}/modify  → ust20002 정정
  POST /api/v1/orders/{id}/cancel  → ust20003 취소
  GET  /api/v1/orders/{id}         → ust21510 당일 주문체결 확인 (+ ust21150 일별)
  GET  /api/v1/orders?status=      → ust21050 미체결 / ust21510 체결

DRY_RUN=true 이면 주문 4종과 잔고·예수금은 키움에 보내지 않고 봇 내부 가상 계좌로 처리합니다.
"""
import asyncio
import json
import re
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import config
import store
from kiwoom_us_client import KiwoomApiError, KiwoomUsClient, anum, parse_ts14, split_hms
from logger import get_logger

log = get_logger("bridge")
ET = ZoneInfo("America/New_York")
KST = ZoneInfo("Asia/Seoul")

# 엔진이 사용하는 토스 주소 목록 (engine_sync.py 의 호환성 점검 기준)
ROUTES = (
    ("GET", r"^/api/v1/accounts$", "_r_accounts"),
    ("GET", r"^/api/v1/commissions$", "_r_commissions"),
    ("GET", r"^/api/v1/prices$", "_r_prices"),
    ("GET", r"^/api/v1/trades$", "_r_trades"),
    ("GET", r"^/api/v1/orderbook$", "_r_orderbook"),
    ("GET", r"^/api/v1/candles$", "_r_candles"),
    ("GET", r"^/api/v1/exchange-rate$", "_r_fx"),
    ("GET", r"^/api/v1/market-calendar/US$", "_r_calendar"),
    ("GET", r"^/api/v1/holdings$", "_r_holdings"),
    ("GET", r"^/api/v1/buying-power$", "_r_buying_power"),
    ("GET", r"^/api/v1/sellable-quantity$", "_r_sellable"),
    ("POST", r"^/api/v1/orders$", "_r_create_order"),
    ("POST", r"^/api/v1/orders/(?P<oid>[^/]+)/modify$", "_r_modify_order"),
    ("POST", r"^/api/v1/orders/(?P<oid>[^/]+)/cancel$", "_r_cancel_order"),
    ("GET", r"^/api/v1/orders/(?P<oid>[^/]+)$", "_r_order_detail"),
    ("GET", r"^/api/v1/orders$", "_r_orders"),
)
_COMPILED = [(m, re.compile(p), h) for m, p, h in ROUTES]

# 키움 오류 메시지 → 엔진이 이해하는 토스 오류 코드
_ERR_KEYWORDS = (
    (("주문가능시간", "장종료", "장 종료", "장시작", "장 시작", "거래시간", "주문시간", "휴장"), "order-hours-closed"),
    (("거래정지", "매매정지", "거래 정지", "거래제한"), "stock-restricted"),
    (("점검",), "maintenance"),
    (("증거금", "주문가능금액", "예수금", "잔고부족", "금액이 부족", "부족"), "insufficient-buying-power"),
    (("매도가능수량", "주문가능수량", "수량이 부족", "보유수량"), "insufficient-sellable-quantity"),
    (("원주문", "정정취소", "정정/취소", "취소가능", "이미 체결", "미체결"), "order-not-modifiable"),
    (("유량", "초과", "요청 건수", "1700"), "rate-limited"),
)
TERMINAL = ("FILLED", "CANCELED", "REJECTED", "REPLACED")


def _iso_kst(dt: datetime) -> str:
    return dt.astimezone(KST).isoformat(timespec="seconds")


def _s(v) -> str:
    return str(v if v is not None else "").strip()


class TossBridge:
    def __init__(self, kiw: KiwoomUsClient = None, error_cls=None, dry_run: bool = None):
        self.kiw = kiw or KiwoomUsClient()
        self.error_cls = error_cls or RuntimeError       # 엔진의 _NonRetryableApiError 가 주입됩니다
        self.dry_run = config.DRY_RUN if dry_run is None else dry_run
        self.context_provider = lambda: {}               # 체결 순간의 상황 스냅샷 (trade_context.snapshot)
        self.on_fill = None                              # 체결 알림 콜백 (선택)
        self.on_api_error = None                         # API 오류 알림 콜백 (선택)
        self._cache = {}                                 # key → (mono_ts, value)
        self._c1m = {}                                   # symbol → {naive_ts: bar}
        self._c1m_refreshed = {}                         # symbol → mono
        self._c1m_raw = {}                               # symbol → {key: (o,h,l,c)} 분봉 원본값 (이상 가격 필터용)
        self._chart_tz = None                            # 감지된 분봉 시간대
        self._locks = {}
        self._seq = 0
        self.last_error = ""
        self.stats = {"req": 0, "err": 0, "orders": 0}

    # ══ 진입점 : 엔진의 TossApiClient._request 가 이 함수로 교체됩니다 ═════
    async def request(self, method: str, path: str, params=None, json_data=None, **_ignored) -> dict:
        m = str(method or "GET").upper()
        p = path if str(path).startswith("/") else f"/{path}"
        p = p.split("?", 1)[0]
        self.stats["req"] += 1
        for rm, rx, handler in _COMPILED:
            if rm != m:
                continue
            mt = rx.match(p)
            if not mt:
                continue
            try:
                result = await getattr(self, handler)(params or {}, json_data or {}, **mt.groupdict())
                return {"result": result}
            except KiwoomApiError as e:
                self.stats["err"] += 1
                self.last_error = f"{datetime.now(KST):%m-%d %H:%M:%S} {e}"
                code = self._map_error(e)
                if self.on_api_error:
                    try:
                        self.on_api_error(e.api_id, code, e.msg + (f"\n💡 {e.hint}" if e.hint else ""),
                                          getattr(e, "transient", False))
                    except Exception:
                        pass
                raise self._err(e.http_status if e.http_status >= 400 else 422, code, f"[키움 {e.api_id}] {e.msg}")
        raise self._err(404, "kiwoom-bridge-unmapped-endpoint",
                        f"브릿지에 없는 토스 주소입니다: {m} {p} (toss_bridge.py ROUTES 보강 필요)")

    def _err(self, status: int, code: str, msg: str):
        try:
            return self.error_cls(status, code, msg, {})
        except TypeError:
            return self.error_cls(f"[HTTP {status} · {code}] {msg}")

    @staticmethod
    def _map_error(e: KiwoomApiError) -> str:
        msg = e.msg or ""
        for words, code in _ERR_KEYWORDS:
            if any(w in msg for w in words):
                return code
        return f"kiwoom-{e.code}"

    # ── 공통 도우미 ───────────────────────────────────────────
    async def _call(self, fn, *args, **kw):
        return await asyncio.to_thread(fn, *args, **kw)

    # 일시 오류 때 직전 값을 대신 써도 되는 조회와 그 허용 나이(초). 현재가·잔고·주문 조회는 대상이 아닙니다.
    STALE_OK = {"d1:": 3 * 86400, "fx": 86400, "tk:": 15, "ob:": 5}

    async def _cached(self, key: str, ttl: float, fn, *args, **kw):
        hit = self._cache.get(key)
        now = time.monotonic()
        if hit and now - hit[0] < ttl:
            return hit[1]
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            hit = self._cache.get(key)
            if hit and time.monotonic() - hit[0] < ttl:
                return hit[1]
            try:
                val = await self._call(fn, *args, **kw)
            except KiwoomApiError as e:
                max_age = next((v for p, v in self.STALE_OK.items() if key.startswith(p)), 0)
                if hit and getattr(e, "transient", False) and time.monotonic() - hit[0] < max_age:
                    log.warning("키움 일시 오류로 직전 값 사용 (%s, %.0f초 전 값): %s", key,
                                time.monotonic() - hit[0], e.msg[:80])
                    return hit[1]
                raise
            self._cache[key] = (time.monotonic(), val)
            return val

    def _drop(self, *prefixes):
        for k in list(self._cache):
            if any(k.startswith(p) for p in prefixes):
                self._cache.pop(k, None)

    @staticmethod
    def _k(name: str, default):
        return config.get(f"KIWOOM.{name}", default)

    def fee_rate(self) -> float:
        r = float(config.get("FEE.US_COMMISSION_RATE", 0.0025) or 0.0025)
        return min(max(r, 0.000001), 0.01)

    def _fee(self, side: str, amount: float) -> float:
        extra = float(config.get("FEE.SELL_EXTRA_FEE_RATE", 0.0) or 0.0) if side == "SELL" else 0.0
        return round(amount * (self.fee_rate() + extra), 4)

    # ══ 계좌·수수료 (고정/설정 응답) ══════════════════════════════════
    async def _r_accounts(self, params, body):
        return [{"accountSeq": 1, "accountType": "BROKERAGE", "accountNo": "KIWOOM"}]

    async def _r_commissions(self, params, body):
        return [{"marketCountry": "US", "commissionRate": f"{self.fee_rate():.6f}", "startDate": None,
                 "endDate": None}]

    async def _r_calendar(self, params, body):
        return {}

    # ══ 시세 ══════════════════════════════════════════════════════════
    def _guard(self):
        """이상 가격 필터 (price_guard.py). 설정이 바뀌면 다시 만듭니다."""
        from price_guard import PriceGuard
        cfg = (bool(self._k("PRICE_GUARD_ENABLED", True)), float(self._k("PRICE_GUARD_BAND_PCT", 0.5)),
               float(self._k("PRICE_GUARD_BAR_BAND_PCT", 2.0)))
        g = getattr(self, "_pg", None)
        if g is None or getattr(self, "_pg_cfg", None) != cfg:
            old = g
            g = PriceGuard(band_pct=cfg[1], bar_band_pct=cfg[2], enabled=cfg[0])
            if old is not None:
                g.last_ok, g.minute_range, g.stats = old.last_ok, old.minute_range, old.stats
            self._pg, self._pg_cfg = g, cfg
        return g

    async def _best_bid_ask(self, sym: str, ttl: float):
        ob = await self._cached(f"ob:{sym}", ttl, self.kiw.orderbook, sym)
        bid = ob["bids"][0]["price"] if ob.get("bids") else 0.0
        ask = ob["asks"][0]["price"] if ob.get("asks") else 0.0
        return ob, bid, ask

    async def quote(self, symbol: str) -> dict:
        """현재가. 실측상 키움 현재가에는 호가와 동떨어진 값이 섞이므로(price_guard.py) 호가 TR(usa20101)의
        현재가를 1호가 범위로 검증해 씁니다. 전일종가 등 느린 항목은 현재가 TR(usa20100)을 5초 캐시로 받습니다."""
        sym = symbol.upper()
        ttl = float(self._k("PRICE_CACHE_SEC", 0.35) if sym in ("SOXL",) else self._k("REF_PRICE_CACHE_SEC", 5.0))
        g = self._guard()
        if not g.enabled:
            return await self._cached(f"q:{sym}", ttl, self.kiw.quote, sym)
        base = await self._cached(f"q:{sym}", max(ttl, 5.0), self.kiw.quote, sym)
        try:
            ob, bid, ask = await self._best_bid_ask(sym, ttl)
        except KiwoomApiError as e:
            log.debug("호가 조회 실패 %s (현재가만으로 검증): %s", sym, e)
            ob, bid, ask = {}, 0.0, 0.0
            base = await self._cached(f"q:{sym}", ttl, self.kiw.quote, sym)
        raw = float(ob.get("price") or 0.0) or float(base.get("price") or 0.0)
        mk = datetime.now(self._tz()).strftime("%Y%m%d%H%M00")
        px = g.check(sym, raw, bid, ask, mk)
        if px != raw:
            now = time.monotonic()
            if now - getattr(self, "_pg_logged", 0.0) > 60:
                self._pg_logged = now
                log.warning("이상 가격 보정 %s: 키움 현재가 %.4f → %.4f (매수1호가 %.4f / 매도1호가 %.4f, 누적 %d건)",
                            sym, raw, px, bid, ask, g.stats["fixed"])
        return {**base, "price": px, "raw_price": raw, "bid": bid, "ask": ask}

    async def _r_prices(self, params, body):
        out = []
        for sym in [x.strip().upper() for x in _s(params.get("symbols")).split(",") if x.strip()]:
            try:
                q = await self.quote(sym)
            except KiwoomApiError as e:
                log.debug("현재가 조회 실패 %s: %s", sym, e)
                continue
            if q["price"] > 0:
                out.append({"symbol": sym, "timestamp": _iso_kst(datetime.now(KST)),
                            "lastPrice": f"{q['price']:.4f}", "currency": "USD"})
        return out

    async def _r_orderbook(self, params, body):
        sym = _s(params.get("symbol")).upper() or "SOXL"
        ob = await self._cached(f"ob:{sym}", 0.3, self.kiw.orderbook, sym)
        return {"timestamp": _iso_kst(datetime.now(KST)), "currency": "USD",
                "asks": [{"price": f"{a['price']:.4f}", "volume": str(int(a["volume"]))} for a in ob["asks"]],
                "bids": [{"price": f"{b['price']:.4f}", "volume": str(int(b["volume"]))} for b in ob["bids"]]}

    async def _r_trades(self, params, body):
        sym = _s(params.get("symbol")).upper() or "SOXL"
        cnt = max(1, min(50, int(float(params.get("count") or 50))))
        rows = await self._cached(f"tk:{sym}", 0.3, self.kiw.ticks, sym)
        g = self._guard()
        bid = ask = 0.0
        if g.enabled:
            try:
                _, bid, ask = await self._best_bid_ask(sym, 0.35)
            except KiwoomApiError:
                pass
        tz = self._tz()
        now_l = datetime.now(tz)
        out = []
        for r in rows[:cnt]:
            hms = split_hms(r["tm"])
            if r["price"] <= 0 or hms is None:
                continue          # 시각 형식이 이상한 행은 버립니다 (엔진은 5초 이내 체결만 사용)
            if not g.tick_ok(r["price"], bid, ask):
                continue          # 호가 범위를 벗어난 체결(실측: 프리장에 1~4% 동떨어진 값 혼입)은 버립니다
            _, hh, mm, ss = hms   # 24시 이상(자정 넘긴 주간거래)은 오늘 날짜의 0시대로
            dt = now_l.replace(hour=hh, minute=mm, second=ss, microsecond=0)
            if dt > now_l + timedelta(minutes=2):
                dt -= timedelta(days=1)
            out.append({"price": f"{r['price']:.4f}", "volume": str(int(r["volume"]) or 1),
                        "timestamp": _iso_kst(dt), "currency": "USD"})
        return out

    async def _r_fx(self, params, body):
        errs = []
        try:
            rate = await self._cached("fx", 60.0, self.kiw.fx_rate)
        except KiwoomApiError as e:
            rate = 0.0
            errs.append(f"ust31301: {e.code} {e.msg}" + (f" → {e.hint}" if e.hint else ""))
            if e.api_id == "au10001" or str(e.code).startswith("8"):
                raise
        if rate <= 100:
            try:
                rate = (await self.quote("SOXL")).get("fx", 0.0)
            except KiwoomApiError as e:
                rate = 0.0
                errs.append(f"usa20100: {e.code} {e.msg}" + (f" → {e.hint}" if e.hint else ""))
        if rate <= 100:
            fb = float(store.kv_get("last_fx_rate", 0) or 0)
            if fb > 100:                       # 마지막으로 성공한 환율로 대신 (환율은 원화 환산 표시에만 쓰입니다)
                rate = fb
            else:
                raise KiwoomApiError("ust31301", "NO_FX", "환율 조회 실패 — " + (" | ".join(errs) or "응답에 환율 없음"))
        else:
            store.kv_set("last_fx_rate", rate)
        return {"baseCurrency": "USD", "quoteCurrency": "KRW", "rate": f"{rate:.2f}", "midRate": f"{rate:.2f}"}

    # ── 분봉 시간대 판정 ──────────────────────────────────────
    def _tz(self):
        mode = str(self._k("CHART_TZ", "AUTO") or "AUTO").upper()
        if mode == "KST":
            return KST
        if mode in ("ET", "EST", "EDT"):
            return ET
        return self._chart_tz or ET

    def _detect_chart_tz(self, rows: list):
        """영업일자(bus_dt)와 체결시각 날짜가 다른 봉이 있으면 한국시간, 아니면 미국 동부시간으로 판정"""
        if self._chart_tz is not None or not rows:
            return
        diff = same = 0
        for r in rows:
            tm, bd = _s(r.get("cntr_tm")), _s(r.get("bus_dt"))
            if len(tm) >= 8 and len(bd) == 8:
                if tm[:8] == bd:
                    same += 1
                else:
                    diff += 1
        self._chart_tz = KST if diff > max(3, same * 0.05) else ET
        newest = max((_s(r.get("cntr_tm")) for r in rows), default="")
        log.info("분봉 시간대 판정: %s (영업일자와 날짜 같은 봉 %d · 다른 봉 %d · 최신 봉 %s)",
                 "KST" if self._chart_tz is KST else "ET", same, diff, newest)

    def newest_candle_age_min(self, symbol: str = "SOXL"):
        bars = self._c1m.get(symbol.upper()) or {}
        if not bars:
            return None
        newest = max(bars)
        try:
            dt = datetime.strptime(newest, "%Y%m%d%H%M%S").replace(tzinfo=self._tz())
        except ValueError:
            return None
        return (datetime.now(ET) - dt.astimezone(ET)).total_seconds() / 60.0

    # ══ 캔들 ══════════════════════════════════════════════════════════
    async def _r_candles(self, params, body):
        sym = _s(params.get("symbol")).upper() or "SOXL"
        interval = _s(params.get("interval")) or "1m"
        count = max(1, min(200, int(float(params.get("count") or 200))))
        before = _s(params.get("before"))
        if interval == "1d":
            return {"candles": await self._daily_candles(sym, count), "nextBefore": None}
        bars = await self._minute_bars(sym, need=400 if not before else 400)
        keys = sorted(bars)
        if before:
            try:
                b_dt = datetime.fromisoformat(before.replace("Z", "+00:00"))
                b_key = b_dt.astimezone(self._tz()).strftime("%Y%m%d%H%M%S")
                keys = [k for k in keys if k < b_key]
            except ValueError:
                pass
        sel = keys[-count:]
        candles = [bars[k] for k in sel]
        next_before = candles[0]["timestamp"] if sel and len(keys) > len(sel) else None
        return {"candles": candles, "nextBefore": next_before}

    async def _minute_bars(self, sym: str, need: int = 400) -> dict:
        ttl = float(self._k("CANDLE_1M_CACHE_SEC", 4.0))
        last = self._c1m_refreshed.get(sym, 0.0)
        store_ = self._c1m.setdefault(sym, {})
        if store_ and time.monotonic() - last < ttl:
            return store_
        lock = self._locks.setdefault(f"c1m:{sym}", asyncio.Lock())
        async with lock:
            if store_ and time.monotonic() - self._c1m_refreshed.get(sym, 0.0) < ttl:
                return store_
            first = len(store_) < 60
            max_pages = int(self._k("CANDLE_MAX_PAGES", 4))
            # 실측(2026-11-11): 시작일자(strt_dt)=그 날짜부터 과거로 거슬러 올라가는 기준일, 응답은 최신→과거 100봉씩.
            # 오늘(ET) 날짜를 주고 첫 기동 때만 여러 페이지(=400봉), 이후엔 최신 1페이지만 받아 덧붙입니다.
            today = datetime.now(ET).strftime("%Y%m%d")
            try:
                rows = await self._call(self.kiw.minute_chart, sym, today, max_pages if first else 1)
            except KiwoomApiError as e:
                if store_:
                    log.debug("분봉 갱신 실패(기존 캐시 유지) %s: %s", sym, e)
                    self._c1m_refreshed[sym] = time.monotonic() - ttl / 2
                    return store_
                raise
            self._detect_chart_tz(rows)
            tz = self._tz()
            end_label = bool(self._k("CANDLE_TIME_IS_END", False))
            raw_ = self._c1m_raw.setdefault(sym, {})
            meta, touched = {}, []
            for r in rows:
                c = anum(r.get("cur_prc"))
                naive = parse_ts14(_s(r.get("cntr_tm")))     # 24시 이상 표기(자정 넘긴 주간거래)도 실제 시각으로
                if naive is None or c <= 0:
                    continue
                dt = naive.replace(second=0, tzinfo=tz)
                if end_label:
                    dt -= timedelta(minutes=1)
                key = dt.strftime("%Y%m%d%H%M%S")
                o = anum(r.get("open_pric")) or c
                raw_[key] = (o, max(anum(r.get("high_pric")) or c, o, c), min(anum(r.get("low_pric")) or c, o, c), c)
                meta[key] = (_iso_kst(dt), str(int(anum(r.get("trde_qty")))))
                touched.append(key)
            # 이상 가격 필터: 이번에 받은 봉 + 그 앞 5봉(뒤 봉이 생기면 중앙값이 달라지므로)을 다시 계산
            order_ = sorted(raw_)
            if touched:
                i0 = max(0, order_.index(min(touched)) - 5)
                redo = [k for k in order_[i0:] if k in store_ or k in meta]
                for key, (o, h, l, c) in self._guard().fix_bars(sym, raw_, redo).items():
                    ts, vol = meta.get(key) or (store_[key]["timestamp"], store_[key]["volume"])
                    store_[key] = {"timestamp": ts, "openPrice": f"{o:.4f}", "highPrice": f"{h:.4f}",
                                   "lowPrice": f"{l:.4f}", "closePrice": f"{c:.4f}", "volume": vol,
                                   "currency": "USD"}
            if len(store_) > 2400:                     # 메모리 상한: 최근 봉만 유지
                for k in sorted(store_)[:-2000]:
                    store_.pop(k, None)
                    raw_.pop(k, None)
            self._c1m_refreshed[sym] = time.monotonic()
            return store_

    async def _daily_candles(self, sym: str, count: int) -> list:
        start = datetime.now(ET).strftime("%Y%m%d")      # 실측: 시작일자 = 이 날짜부터 과거로 (예전 날짜를 주면 그때까지의 봉만 옴)
        rows = await self._cached(f"d1:{sym}", float(self._k("CANDLE_1D_CACHE_SEC", 300)),
                                  self.kiw.daily_chart, sym, start)
        out = {}
        for r in rows:
            d = re.sub(r"\D", "", _s(r.get("dt")))[:8]
            c = anum(r.get("cur_prc"))
            if len(d) != 8 or c <= 0:
                continue
            o = anum(r.get("open_pric")) or c
            out[d] = {"timestamp": f"{d[:4]}-{d[4:6]}-{d[6:]}T09:00:00+09:00", "openPrice": f"{o:.4f}",
                      "highPrice": f"{max(anum(r.get('high_pric')) or c, o, c):.4f}",
                      "lowPrice": f"{min(anum(r.get('low_pric')) or c, o, c):.4f}", "closePrice": f"{c:.4f}",
                      "volume": str(int(anum(r.get("acc_trde_qty")))), "currency": "USD"}
        return [out[k] for k in sorted(out)][-count:]

    # ══ 잔고·예수금 ═══════════════════════════════════════════════════
    async def broker_holdings(self) -> dict:
        if self.dry_run:
            return await self._paper_holdings()
        return await self._cached("hold", float(self._k("HOLDINGS_CACHE_SEC", 0.8)), self.kiw.holdings)

    async def broker_cash(self) -> dict:
        if self.dry_run:
            p = self._paper()
            return {"cash": p["cash"], "orderable": p["cash"], "withdrawable": p["cash"]}
        return await self._cached("dep", float(self._k("DEPOSIT_CACHE_SEC", 1.5)), self.kiw.deposit_usd)

    async def _r_holdings(self, params, body):
        want = _s(params.get("symbol")).upper()
        h = await self.broker_holdings()
        items = []
        for sym, it in h["items"].items():
            if want and sym != want:
                continue
            rate = (it["eval_amount"] - it["purchase_amount"]) / it["purchase_amount"] if it["purchase_amount"] else 0
            items.append({"symbol": sym, "quantity": str(it["qty"]),
                          "averagePurchasePrice": f"{it['avg_price']:.4f}", "lastPrice": f"{it['last_price']:.4f}",
                          "currency": "USD",
                          "marketValue": {"purchaseAmount": f"{it['purchase_amount']:.4f}",
                                          "amount": f"{it['eval_amount']:.4f}"},
                          "profitLoss": {"amount": f"{it['eval_amount'] - it['purchase_amount']:.4f}",
                                         "rate": f"{rate:.6f}"}})
        return {"items": items}

    async def _r_buying_power(self, params, body):
        cash = await self.broker_cash()
        bp = max(0.0, float(cash.get("orderable") or 0.0))
        cap = float(config.get("SAFETY.MAX_BOT_BUYING_POWER_USD", 0) or 0)
        if cap > 0:
            bp = min(bp, cap)
        return {"currency": "USD", "cashBuyingPower": f"{bp:.4f}"}

    async def _r_sellable(self, params, body):
        sym = _s(params.get("symbol")).upper() or "SOXL"
        h = await self.broker_holdings()
        it = h["items"].get(sym)
        return {"symbol": sym, "sellableQuantity": str(int(it["sellable"]) if it else 0)}

    # ══ 주문 ══════════════════════════════════════════════════════════
    def _new_id(self, ord_no: str) -> str:
        if self.dry_run:
            self._seq += 1
            return f"D{int(time.time() * 1000)}-{self._seq}"
        return f"K{datetime.now(KST):%Y%m%d}-{ord_no}"

    def _snapshot(self) -> dict:
        try:
            return self.context_provider() or {}
        except Exception:
            return {}

    async def _r_create_order(self, params, body):
        sym, side = _s(body.get("symbol")).upper(), _s(body.get("side")).upper()
        qty = int(float(body.get("quantity") or 0))
        price_s = _s(body.get("price"))
        price = float(price_s or 0)
        if sym == "" or side not in ("BUY", "SELL") or qty <= 0 or price <= 0:
            raise self._err(400, "invalid-request", f"주문 값 오류: {body}")
        max_usd = float(config.get("SAFETY.MAX_ORDER_USD", 0) or 0)
        if max_usd > 0 and side == "BUY" and qty * price > max_usd:
            raise self._err(422, "order-amount-exceeded",
                            f"1회 주문 상한 초과: {qty}주×${price} > ${max_usd:,.0f} (config.json SAFETY.MAX_ORDER_USD)")
        ctx = self._snapshot()
        if self.dry_run:
            oid = self._paper_place(sym, side, qty, price, _s(body.get("clientOrderId")), ctx)
        else:
            fn = self.kiw.buy if side == "BUY" else self.kiw.sell
            res = await self._call(fn, sym, qty, price_s)
            if not res["ord_no"]:
                raise self._err(502, "kiwoom-no-order-number", f"주문번호가 없습니다: {res['msg']}")
            oid = self._new_id(res["ord_no"])
            store.add_order(oid, res["ord_no"], "NEW", sym, side, qty, price, _s(body.get("clientOrderId")),
                            context=ctx)
            self._drop("ord:", "hold", "dep")
        self.stats["orders"] += 1
        log.info("주문 접수 %s %s %d주 @%s → %s%s", side, sym, qty, price_s, oid, " (DRY_RUN)" if self.dry_run else "")
        return {"orderId": oid, "clientOrderId": _s(body.get("clientOrderId")) or None}

    async def _r_modify_order(self, params, body, oid):
        row = store.get_order(oid)
        if not row:
            raise self._err(404, "order-not-found", f"모르는 주문: {oid}")
        price_s = _s(body.get("price"))
        if self.dry_run:
            store.update_order(oid, status="REPLACED", closed_ts=time.time())
            remain = row["qty"] - row["filled_qty"]
            new_id = self._paper_place(row["symbol"], row["side"], remain, float(price_s), "", self._snapshot(),
                                       kind="MODIFY", parent=oid)
            return {"orderId": new_id}
        res = await self._call(self.kiw.modify, row["symbol"], row["ord_no"], price_s)
        new_id = self._new_id(res["ord_no"] or f"M{row['ord_no']}")
        store.add_order(new_id, res["ord_no"], "MODIFY", row["symbol"], row["side"],
                        max(0, row["qty"] - row["filled_qty"]), float(price_s), parent_id=oid,
                        context=self._snapshot())
        store.update_order(oid, note=f"정정→{new_id}")
        self._drop("ord:")
        return {"orderId": new_id}

    async def _r_cancel_order(self, params, body, oid):
        row = store.get_order(oid)
        if self.dry_run:
            if row and row["status"] not in TERMINAL:
                store.update_order(oid, status="CANCELED", closed_ts=time.time())
            return {"orderId": f"{oid}-C"}
        if row:
            sym, ord_no = row["symbol"], row["ord_no"]
        else:
            sym, ord_no = "SOXL", oid.split("-", 1)[-1]      # 사용자가 영웅문에서 직접 낸 주문
        res = await self._call(self.kiw.cancel, sym, ord_no)
        if row and row["status"] not in TERMINAL:
            store.update_order(oid, status="PENDING_CANCEL", note=f"취소요청 {res['ord_no']}")
        self._drop("ord:", "hold", "dep")
        return {"orderId": f"K{datetime.now(KST):%Y%m%d}-{res['ord_no'] or 'C' + ord_no}"}

    # ── 키움 주문행 → 묶음 ────────────────────────────────────
    async def _order_rows(self, symbol: str) -> list:
        return await self._cached(f"ord:today:{symbol}", float(self._k("ORDERS_CACHE_SEC", 0.35)),
                                  self.kiw.orders_today, symbol)

    async def _order_rows_on(self, date_kst: str, symbol: str) -> list:
        return await self._cached(f"ord:{date_kst}:{symbol}", 20.0, self.kiw.orders_on, date_kst, symbol)

    @staticmethod
    def _group(rows: list) -> dict:
        g = {}
        for r in rows:
            no = _s(r.get("ord_no"))
            if no:
                g.setdefault(no, []).append(r)
        return g

    @staticmethod
    def _summarize(rs: list) -> dict:
        """같은 주문번호의 행(1개 또는 체결 건별 여러 개)을 주문 1건으로 요약"""
        r0 = rs[0]
        oq = max(int(anum(r.get("ord_qty"))) for r in rs)
        fills = [(int(anum(r.get("cntr_qty"))), anum(r.get("cntr_uv"))) for r in rs]
        f_sum, f_max = sum(q for q, _ in fills), max(q for q, _ in fills)
        if len(rs) > 1 and 0 < f_sum <= oq and len({_s(r.get("cntr_time")) for r in rs}) > 1:
            fq = f_sum
            avg = sum(q * p for q, p in fills) / f_sum if f_sum else 0.0
        else:
            fq = f_max
            avg = next((p for q, p in fills if q == f_max and p > 0), 0.0)
        cncl = max(int(anum(r.get("cncl_qty"))) for r in rs)
        mdfy = max(int(anum(r.get("mdfy_qty"))) for r in rs)
        # 주문잔량 필드가 응답에 없으면(명세와 다를 때) 0 으로 오인해 '취소'로 잘못 판정하지 않도록 직접 계산
        has_remn = all("ord_remnq" in r and _s(r.get("ord_remnq")) != "" for r in rs)
        remn = min(int(anum(r.get("ord_remnq"))) for r in rs) if has_remn else max(0, oq - fq - cncl)
        stat = " ".join(_s(r.get("ord_stat") or r.get("ord_stat_nm")) for r in rs)
        side_raw = _s(r0.get("slby_tp")) or _s(r0.get("slby_tp_nm"))
        side = "SELL" if side_raw in ("1",) or "매도" in side_raw else "BUY"
        if oq > 0 and fq >= oq:
            status = "FILLED"
        elif "거부" in stat:
            status = "REJECTED"
        elif mdfy > 0 and remn <= 0:
            status = "REPLACED"
        elif remn <= 0 and (cncl > 0 or "취소" in stat or has_remn):
            status = "CANCELED"
        elif fq > 0:
            status = "PARTIAL_FILLED"
        else:
            status = "PENDING"
        return {"ord_no": _s(r0.get("ord_no")), "orig_ord_no": _s(r0.get("orig_ord_no")),
                "symbol": _s(r0.get("stk_cd")).upper(), "side": side, "qty": oq, "price": anum(r0.get("ord_uv")),
                "filled": fq, "avg": avg, "remain": remn, "status": status,
                "kind": _s(r0.get("ord_cntr_tp")), "ord_time": _s(r0.get("ord_time")), "stat": stat,
                "fill_time": max((_s(r.get("cntr_time")) for r in rs), default="")}

    def _toss_order(self, oid: str, s: dict, placed_iso: str = None) -> dict:
        amount = round(s["filled"] * s["avg"], 4)
        return {"orderId": oid, "symbol": s["symbol"], "side": s["side"], "orderType": "LIMIT",
                "timeInForce": "DAY", "status": s["status"], "price": f"{s['price']:.4f}",
                "quantity": str(s["qty"]), "orderAmount": None, "currency": "USD",
                "orderedAt": placed_iso or _iso_kst(datetime.now(KST)), "canceledAt": None,
                "execution": {"filledQuantity": str(s["filled"]),
                              "averageFilledPrice": f"{s['avg']:.4f}" if s["filled"] else None,
                              "filledAmount": f"{amount:.4f}" if s["filled"] else None,
                              "commission": f"{self._fee(s['side'], amount):.4f}" if s["filled"] else "0",
                              "tax": "0", "filledAt": _iso_kst(datetime.now(KST)) if s["filled"] else None,
                              "settlementDate": None}}

    def _row_to_summary(self, row: dict) -> dict:
        return {"ord_no": row["ord_no"], "orig_ord_no": "", "symbol": row["symbol"], "side": row["side"],
                "qty": row["qty"], "price": row["price"], "filled": row["filled_qty"],
                "avg": row["avg_fill_price"], "remain": max(0, row["qty"] - row["filled_qty"]),
                "status": row["status"], "kind": row["kind"], "ord_time": row["placed_kst"][11:], "fill_time": "",
                "stat": ""}

    def _apply(self, oid: str, row: dict, s: dict):
        """키움 조회 결과를 기록에 반영하고, 새로 체결된 수량만큼 체결 기록을 남깁니다."""
        status = s["status"]
        if row["status"] == "PENDING_CANCEL" and status in ("PENDING", "PARTIAL_FILLED"):
            status = "PENDING_CANCEL"
        new_fill = s["filled"] - int(row["recorded_fill_qty"] or 0)
        upd = {}
        if new_fill > 0:
            prev_amt = (row["recorded_fill_qty"] or 0) * (row["avg_fill_price"] or 0)
            px = (s["filled"] * s["avg"] - prev_amt) / new_fill if s["avg"] > 0 else row["price"]
            px = px if px > 0 else (s["avg"] or row["price"])
            ctx = self._snapshot()
            store.add_fill(oid, row["symbol"], row["side"], new_fill, round(px, 4),
                           self._fee(row["side"], new_fill * px), limit_price=row["price"],
                           lot_type=_s(ctx.get("pending_lot_type") if row["side"] == "BUY"
                                       else ctx.get("pending_sell_mode")),
                           dry_run=bool(row["dry_run"]), context=ctx)
            upd.update(filled_qty=s["filled"], avg_fill_price=round(s["avg"] or px, 4),
                       recorded_fill_qty=s["filled"])
            self._drop("hold", "dep")
            if self.on_fill:
                try:
                    self.on_fill(row["symbol"], row["side"], new_fill, round(px, 4), oid)
                except Exception:
                    pass
        if status != row["status"]:
            upd["status"] = status
            if status in TERMINAL:
                upd["closed_ts"] = time.time()
        if upd:
            store.update_order(oid, **upd)
            row.update(upd)

    async def _refresh(self, oid: str, row: dict) -> dict:
        """주문 1건의 최신 상태 요약 (기록 갱신 포함)"""
        if self.dry_run or row["dry_run"]:
            await self._paper_match()
            row = store.get_order(oid) or row
            return self._row_to_summary(row)
        if row["status"] in TERMINAL:
            return self._row_to_summary(row)
        today = datetime.now(KST).strftime("%Y%m%d")
        g = self._group(await self._order_rows(row["symbol"]))
        rs = g.get(row["ord_no"])
        if not rs and (row["date_kst"] != today or time.time() - (row["placed_ts"] or 0) > 20):
            for d_key in dict.fromkeys((row["date_kst"], row["date_et"])):   # 키움의 주문일자 기준(한국/미국)이 불명확 → 둘 다 조회
                g2 = self._group(await self._order_rows_on(d_key, row["symbol"]))
                if g2.get(row["ord_no"]):
                    g, rs = g2, g2[row["ord_no"]]
                    break
        if not rs:
            age = time.time() - (row["placed_ts"] or 0)
            if age > 30 and int(age) % 60 < 2:
                log.warning("주문 %s(%s) 이 키움 체결조회에 보이지 않습니다 (%.0f초 경과)", oid, row["ord_no"], age)
            return self._row_to_summary(row)
        s = self._summarize(rs)
        s["symbol"] = s["symbol"] or row["symbol"]
        s["qty"] = s["qty"] or row["qty"]
        s["price"] = s["price"] or row["price"]
        if s["status"] in ("PENDING", "PARTIAL_FILLED"):
            # 키움이 원주문 행을 그대로 두고 정정(11)/취소(12) 행을 따로 만드는 경우: 자식 행으로 종료 판정
            for c_rs in g.values():
                c = self._summarize(c_rs)
                if c["orig_ord_no"] == row["ord_no"] and c["ord_no"] != row["ord_no"] and "거부" not in c["stat"]:
                    if c["kind"] == "12":
                        s["status"] = "CANCELED"
                    elif c["kind"] == "11":
                        s["status"] = "REPLACED"
        self._apply(oid, row, s)
        s["status"] = row["status"]
        return s

    async def _r_order_detail(self, params, body, oid):
        row = store.get_order(oid)
        if row:
            s = await self._refresh(oid, row)
            return self._toss_order(oid, s, placed_iso=row["placed_kst"].replace(" ", "T") + "+09:00")
        if self.dry_run or "-" not in oid:
            raise self._err(404, "order-not-found", f"모르는 주문: {oid}")
        date_kst, ord_no = oid[1:].split("-", 1)                  # 영웅문에서 직접 낸 주문
        g = self._group(await self._order_rows("SOXL"))
        rs = g.get(ord_no) or self._group(await self._order_rows_on(date_kst, "SOXL")).get(ord_no)
        if not rs:
            raise self._err(404, "order-not-found", f"키움 조회에 없는 주문: {oid}")
        return self._toss_order(oid, self._summarize(rs))

    async def _r_orders(self, params, body):
        status = _s(params.get("status")).upper() or "OPEN"
        sym = _s(params.get("symbol")).upper()
        limit = max(1, min(100, int(float(params.get("limit") or 20))))
        if self.dry_run:
            await self._paper_match()
            rows = store.recent_orders(200, sym)
            if status == "OPEN":
                rows = [r for r in rows if r["status"] not in TERMINAL]
            else:
                rows = [r for r in rows if r["status"] in TERMINAL]
            return {"orders": [self._toss_order(r["order_id"], self._row_to_summary(r),
                                                r["placed_kst"].replace(" ", "T") + "+09:00")
                               for r in rows[:limit]], "nextCursor": None, "hasNext": False}
        today = datetime.now(KST)
        t_key = today.strftime("%Y%m%d")
        if status == "OPEN":
            g = self._group(await self._cached(f"ord:open:{sym}", float(self._k("ORDERS_CACHE_SEC", 0.35)),
                                               self.kiw.open_orders, sym))
            batches = [(t_key, g)]
        else:
            batches = [(t_key, self._group(await self._order_rows(sym or "SOXL")))]
            if today.hour < 10:                 # 미국 장은 한국 자정을 넘기므로 어제 날짜 주문도 함께 봅니다
                y_key = (today - timedelta(days=1)).strftime("%Y%m%d")
                try:
                    batches.append((y_key, self._group(await self._order_rows_on(y_key, sym or "SOXL"))))
                except KiwoomApiError as e:
                    log.debug("전일 주문 조회 생략: %s", e)
        sig_map = store.kv_get("manual_order_ids", {}) or {}
        changed, seen, out = False, set(), []
        for d_key, g in batches:
            known = {r["ord_no"]: r for r in store.query(
                "SELECT * FROM orders WHERE date_kst=? AND dry_run=0", (d_key,))}
            for ord_no, rs in g.items():
                s = self._summarize(rs)
                if sym and s["symbol"] and s["symbol"] != sym:
                    continue
                if s["kind"] == "12":
                    continue                                # 취소 접수행 자체는 주문 목록에서 제외
                is_open = s["status"] in ("PENDING", "PARTIAL_FILLED")
                if (status == "OPEN") != is_open:
                    continue
                sig = f"{ord_no}|{s['side']}|{s['qty']}|{s['ord_time']}"
                if sig in seen:
                    continue
                seen.add(sig)
                row = known.get(ord_no)
                if row:
                    oid = row["order_id"]
                    if row["status"] not in TERMINAL:
                        self._apply(oid, row, s)
                else:
                    oid = sig_map.get(sig)
                    if not oid:
                        oid = f"K{d_key}-{ord_no}"
                        sig_map[sig] = oid
                        changed = True
                out.append((s["fill_time"] or s["ord_time"], d_key, self._toss_order(oid, s)))
        if changed:
            if len(sig_map) > 600:
                sig_map = dict(list(sig_map.items())[-400:])
            store.kv_set("manual_order_ids", sig_map)
        out.sort(key=lambda x: (x[1], x[0]), reverse=True)
        return {"orders": [o for _, _, o in out[:limit]], "nextCursor": None, "hasNext": False}

    # ══ DRY_RUN 가상 계좌 ═════════════════════════════════════════════
    def _paper(self) -> dict:
        p = store.kv_get("paper")
        if not isinstance(p, dict) or "cash" not in p:
            p = {"cash": float(config.get("SAFETY.DRY_RUN_START_CASH_USD", 10000) or 10000), "pos": {}}
            store.kv_set("paper", p)
        return p

    def _paper_place(self, sym, side, qty, price, cid, ctx, kind="NEW", parent="") -> str:
        p = self._paper()
        if side == "BUY" and qty * price * (1 + self.fee_rate()) > p["cash"] + 1e-6:
            raise self._err(422, "insufficient-buying-power", f"[DRY_RUN] 가상 예수금 부족 (${p['cash']:,.2f})")
        if side == "SELL":
            held = int(p["pos"].get(sym, {}).get("qty", 0))
            locked = sum(r["qty"] - r["filled_qty"] for r in store.open_orders()
                         if r["dry_run"] and r["symbol"] == sym and r["side"] == "SELL")
            if qty > held - locked:
                raise self._err(422, "insufficient-sellable-quantity", f"[DRY_RUN] 매도가능수량 부족 ({held - locked}주)")
        oid = self._new_id("")
        store.add_order(oid, oid[-9:], kind, sym, side, qty, price, cid, parent_id=parent, dry_run=True, context=ctx)
        return oid

    async def _paper_match(self):
        """가상 주문 체결 판정: 매수는 현재가 ≤ 지정가, 매도는 현재가 ≥ 지정가일 때 지정가로 전량 체결"""
        opens = [r for r in store.open_orders() if r["dry_run"]]
        if not opens:
            return
        p = self._paper()
        for r in opens:
            try:
                px = (await self.quote(r["symbol"]))["price"]
            except KiwoomApiError:
                continue
            if px <= 0:
                continue
            if r["status"] == "PENDING_CANCEL":
                store.update_order(r["order_id"], status="CANCELED", closed_ts=time.time())
                continue
            hit = px <= r["price"] + 1e-9 if r["side"] == "BUY" else px >= r["price"] - 1e-9
            if not hit:
                continue
            qty, fpx = r["qty"] - r["filled_qty"], r["price"]
            amt = qty * fpx
            fee = self._fee(r["side"], amt)
            pos = p["pos"].setdefault(r["symbol"], {"qty": 0, "cost": 0.0})
            if r["side"] == "BUY":
                p["cash"] = round(p["cash"] - amt - fee, 4)
                pos["qty"] += qty
                pos["cost"] = round(pos["cost"] + amt, 4)
            else:
                avg = pos["cost"] / pos["qty"] if pos["qty"] else 0.0
                p["cash"] = round(p["cash"] + amt - fee, 4)
                pos["qty"] -= qty
                pos["cost"] = round(max(0.0, pos["cost"] - avg * qty), 4)
                if pos["qty"] <= 0:
                    p["pos"].pop(r["symbol"], None)
            store.kv_set("paper", p)
            self._apply(r["order_id"], r, {"filled": r["qty"], "avg": fpx, "status": "FILLED", "remain": 0})

    async def _paper_holdings(self) -> dict:
        await self._paper_match()
        p = self._paper()
        items = {}
        for sym, pos in p["pos"].items():
            if pos["qty"] <= 0:
                continue
            try:
                last = (await self.quote(sym))["price"]
            except KiwoomApiError:
                last = 0.0
            avg = pos["cost"] / pos["qty"]
            last = last or avg
            locked = sum(r["qty"] - r["filled_qty"] for r in store.open_orders()
                         if r["dry_run"] and r["symbol"] == sym and r["side"] == "SELL")
            items[sym] = {"symbol": sym, "qty": pos["qty"], "sellable": max(0, pos["qty"] - locked),
                          "avg_price": round(avg, 4), "last_price": last, "purchase_amount": pos["cost"],
                          "eval_amount": round(pos["qty"] * last, 4), "pnl": round(pos["qty"] * last - pos["cost"], 4),
                          "pnl_pct": round((last / avg - 1) * 100, 2) if avg else 0.0, "name": sym, "fx": 0.0}
        ev = sum(i["eval_amount"] for i in items.values())
        pa = sum(i["purchase_amount"] for i in items.values())
        return {"summary": {"eval_amount": ev, "purchase_amount": pa, "pnl": ev - pa,
                            "pnl_pct": (ev / pa - 1) * 100 if pa else 0.0, "today_realized": 0.0}, "items": items}

    def paper_reset(self, cash: float = None):
        store.kv_set("paper", {"cash": float(cash if cash is not None else
                                             config.get("SAFETY.DRY_RUN_START_CASH_USD", 10000)), "pos": {}})


def describe_routes() -> str:
    return json.dumps([{"method": m, "pattern": p} for m, p, _ in ROUTES], ensure_ascii=False)
