# -*- coding: utf-8 -*-
"""
kiw_soxl_trader - 이상 가격 필터 (키움 블록 · v1.0.12)

배경(실측): 키움 미국주식 프리장 시세는 현재가(usa20100/usa20101 cur_prc)·체결틱(usa20150)·1분봉(usa06011)에
  실제 호가와 1~6% 동떨어진 값이 섞여 옵니다.
    - 2026-11-17 04:11~04:18 ET: 실거래 $47대인데 분봉 고가·시가·종가에 $50~51 이 번갈아 찍힘 (실전 암살자 오판 → 손실)
    - 2026-11-18 04:07~04:30 ET 2.7초 간격 기록 511건: 매수·매도 1호가는 $52.6~53.2 로 일관,
      현재가는 19% 가 호가 중간값에서 0.5% 넘게 벗어남(최대 −4.2%). 정상 표본은 모두 0.3% 이내.
  호가(1호가)는 일관되므로 호가를 기준으로 거릅니다.

하는 일
  1) 현재가: [매수1호가×(1−band), 매도1호가×(1+band)] 밖이면 가까운 1호가로 바꿉니다.
     호가가 없으면 직전 정상가(60초 이내)에서 1.5% 넘게 벗어난 값을 직전 정상가로 바꿉니다.
  2) 체결틱: 같은 범위 밖의 틱을 버립니다.
  3) 1분봉: (a) 봇이 그 분에 직접 확인한 정상가 범위가 있으면 시·고·저·종을 그 범위(±band)로 자릅니다.
            (b) 없으면(기동 전 과거 봉) 앞뒤 5봉 원본값의 중앙값에서 bar_band 넘게 벗어난 값을 중앙값으로 바꿉니다.
"""
import statistics
import time
from collections import OrderedDict


class PriceGuard:
    def __init__(self, band_pct: float = 0.5, bar_band_pct: float = 2.0, enabled: bool = True):
        self.enabled = bool(enabled)
        self.band = max(0.05, float(band_pct)) / 100.0
        self.bar_band = max(0.3, float(bar_band_pct)) / 100.0
        self.last_ok = {}            # sym -> (price, monotonic)
        self.minute_range = {}       # sym -> OrderedDict(minute_key -> [lo, hi])
        self.stats = {"checked": 0, "fixed": 0, "tick_dropped": 0, "bar_fixed": 0}
        self.last_fix = None         # (sym, raw, fixed, bid, ask, wall_ts)

    # ── 1) 현재가 ───────────────────────────────────────────────
    def band_of(self, bid: float, ask: float):
        """호가가 믿을 만하면 (하한, 상한) 반환, 아니면 None"""
        if bid > 0 and ask > 0 and ask >= bid and (ask - bid) / ((ask + bid) / 2) < 0.03:
            return bid * (1 - self.band), ask * (1 + self.band)
        return None

    def check(self, sym: str, raw: float, bid: float, ask: float, minute_key: str = "") -> float:
        if not self.enabled or raw <= 0:
            return raw
        self.stats["checked"] += 1
        now = time.monotonic()
        rng = self.band_of(bid, ask)
        px = raw
        if rng:
            lo, hi = rng
            if raw < lo:
                px = bid
            elif raw > hi:
                px = ask
        else:
            last = self.last_ok.get(sym)
            if last and now - last[1] < 60 and abs(raw / last[0] - 1) > 0.015:
                px = last[0]
        if px != raw:
            self.stats["fixed"] += 1
            self.last_fix = (sym, raw, px, bid, ask, time.time())
        if rng or px == raw:
            self.last_ok[sym] = (px, now)
        if rng and minute_key:
            d = self.minute_range.setdefault(sym, OrderedDict())
            r = d.get(minute_key)
            if r is None:
                d[minute_key] = [px, px]
                while len(d) > 2400:
                    d.popitem(last=False)
            else:
                r[0], r[1] = min(r[0], px), max(r[1], px)
        return px

    # ── 2) 체결틱 ───────────────────────────────────────────────
    def tick_ok(self, price: float, bid: float, ask: float) -> bool:
        if not self.enabled:
            return True
        rng = self.band_of(bid, ask)
        if rng and not (rng[0] <= price <= rng[1]):
            self.stats["tick_dropped"] += 1
            return False
        return True

    # ── 3) 1분봉 ────────────────────────────────────────────────
    def fix_bars(self, sym: str, raw: dict, keys=None) -> dict:
        """raw: {key: (o,h,l,c)} 전체 원본. keys: 고칠 대상(없으면 전체). 반환 {key: (o,h,l,c)}"""
        out = {}
        if not self.enabled:
            return {k: raw[k] for k in (keys or raw)}
        order = sorted(raw)
        pos = {k: i for i, k in enumerate(order)}
        seen = self.minute_range.get(sym, {})
        for k in (keys or order):
            o, h, l, c = raw[k]
            r = seen.get(k)
            if r:                                            # (a) 직접 확인한 정상가 범위
                lo, hi = r[0] * (1 - self.band), r[1] * (1 + self.band)
                no, nc = min(max(o, lo), hi), min(max(c, lo), hi)
                nh, nl = min(max(h, lo), hi), min(max(l, lo), hi)
            else:                                            # (b) 앞뒤 5봉 중앙값
                i = pos[k]
                vals = [v for kk in order[max(0, i - 5): i + 6] for v in raw[kk]]
                med = statistics.median(vals)
                lo, hi = med * (1 - self.bar_band), med * (1 + self.bar_band)
                f = lambda v: v if lo <= v <= hi else med
                no, nc = f(o), f(c)
                nh, nl = (h if lo <= h <= hi else max(no, nc, med)), (l if lo <= l <= hi else min(no, nc, med))
            nh, nl = max(nh, no, nc), min(nl, no, nc)
            if (no, nh, nl, nc) != (o, h, l, c):
                self.stats["bar_fixed"] += 1
            out[k] = (no, nh, nl, nc)
        return out
