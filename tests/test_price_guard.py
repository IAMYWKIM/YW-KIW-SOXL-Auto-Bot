"""이상 가격 필터 시험 (v1.0.12) — 실제 키움 기록으로 검증
 ① 2026-11-18 04:07~04:30 ET 프리장 기록 511건(현재가·1호가·체결틱)  ② 2026-11-17 04:10~04:22 ET 1분봉(실측 원본)"""
import sys, json, gzip, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from price_guard import PriceGuard
ok = True
def check(n, c, e=""):
    global ok; ok &= bool(c); print(("  ✅ " if c else "  ❌ ") + n, e)

recs = json.load(gzip.open(pathlib.Path(__file__).parent / "data" / "capture_20261118_premarket.json.gz", "rt"))
g = PriceGuard(0.5, 2.0)
bad_raw = bad_fix = tk_all = tk_bad = tk_left = 0
worst = 0.0
for t, qcur, bcur, bid, ask, ticks in recs:
    mid = (bid + ask) / 2
    px = g.check("SOXL", bcur, bid, ask, "20261118" + t[:2] + t[3:5] + "00")
    bad_raw += abs(bcur / mid - 1) > 0.005
    bad_fix += abs(px / mid - 1) > 0.005
    worst = max(worst, abs(px / mid - 1) * 100)
    for p in ticks:
        tk_all += 1; out = abs(p / mid - 1) > 0.006; tk_bad += out
        if g.tick_ok(p, bid, ask): tk_left += out
print(f"  · 현재가 {len(recs)}건 중 이상 {bad_raw}건({bad_raw/len(recs)*100:.0f}%) · 체결틱 {tk_all}건 중 이상 {tk_bad}건")
check("기록에 이상 현재가가 실제로 있음", bad_raw > 50)
check("필터 후 이상 현재가 0건", bad_fix == 0, f"최대 편차 {worst:.2f}%")
check("필터 후 이상 체결틱 0건", tk_left == 0)
g2 = PriceGuard(0.5, 2.0)
check("정상 가격은 그대로", g2.check("SOXL", 52.71, 52.70, 52.74) == 52.71)
check("아래로 벗어나면 매수1호가", g2.check("SOXL", 50.69, 52.70, 52.74) == 52.70)
check("위로 벗어나면 매도1호가", g2.check("SOXL", 49.28, 47.60, 47.62) == 47.62)
check("호가 없을 때: 직전 정상가에서 1.5% 넘게 튀면 직전값", g2.check("SOXL", 50.0, 0, 0) == 47.62)
check("꺼 두면 원본 그대로", PriceGuard(enabled=False).check("SOXL", 50.69, 52.70, 52.74) == 50.69)

# ② 11/17 실측 1분봉 (시, 고, 저, 종) — 실거래는 $47대, $50~51 은 이상값
bars = {"0410": (47.9095, 48.68, 47.84, 47.84), "0411": (47.82, 51.27, 47.7295, 51.18), "0412": (51.14, 51.14, 47.81, 47.8121),
        "0413": (50.62, 50.84, 47.7695, 47.7695), "0414": (47.77, 49.98, 47.75, 47.90), "0415": (49.70, 49.70, 47.50, 47.5382),
        "0416": (47.54, 50.43, 47.45, 47.5102), "0417": (50.31, 51.12, 47.48, 50.29), "0418": (47.49, 50.38, 47.20, 47.205),
        "0419": (47.2082, 47.30, 46.82, 47.04), "0420": (46.98, 47.29, 46.8937, 47.1995), "0421": (47.25, 47.27, 46.9321, 46.98),
        "0422": (46.98, 47.14, 46.7501, 47.00)}
fx = PriceGuard(0.5, 2.0).fix_bars("SOXL", bars)
hi = max(v[1] for v in fx.values())
check("과거 봉(중앙값 방식): $49 넘는 값이 모두 사라짐", hi < 49.0, f"보정 후 최고가 {hi:.2f} (원본 51.27)")
check("정상 봉(04:19~04:22)은 그대로", all(fx[k] == bars[k] for k in ("0419", "0420", "0421", "0422")))
check("저가는 건드리지 않음", all(abs(fx[k][2] - bars[k][2]) < 1e-9 for k in bars))
g3 = PriceGuard(0.5, 2.0)
for p in (47.61, 47.55, 47.52): g3.check("SOXL", p, p - 0.01, p + 0.01, "0415")
f15 = g3.fix_bars("SOXL", bars, ["0415"])["0415"]
check("봇이 직접 본 분(04:15): 확인한 범위로 자름", f15[1] <= 47.62 * 1.005 + 1e-9 and f15[0] <= 47.62 * 1.005 + 1e-9, str(tuple(round(x, 2) for x in f15)))
entry = 47.61
check("11/17 재현: 보정 후 +1% 점화선($48.09)에 닿는 봉 없음(04:15 이후)", all(fx[k][1] < entry * 1.01 for k in ("0415", "0416", "0418", "0419")), )
print("결과:", "모두 통과" if ok else "실패 있음")

# ③ 브릿지 통합: 키움이 호가는 정상인데 현재가만 튀는 값을 줄 때 엔진에 넘어가는 가격
import asyncio, os, tempfile
os.environ.setdefault("DRY_RUN", "true"); os.environ.setdefault("TELEGRAM_ENABLED", "false")
import config
config.DATA_DIR = pathlib.Path(tempfile.mkdtemp()); config.DB_PATH = config.DATA_DIR / "t.db" if hasattr(config, "DB_PATH") else None
import toss_bridge
class FakeKiw:
    cur = 50.69
    def quote(self, s): return {"symbol": s, "price": self.cur, "prev_close": 49.07, "raw": {}}
    def orderbook(self, s): return {"price": self.cur, "bids": [{"price": 52.70, "volume": 55}], "asks": [{"price": 52.74, "volume": 69}]}
    def ticks(self, s): return [{"price": 50.69, "volume": 31, "tm": "041800"}, {"price": 52.71, "volume": 10, "tm": "041800"}]
async def run():
    b = toss_bridge.TossBridge(kiw=FakeKiw(), dry_run=True)
    pr = await b._r_prices({"symbols": "SOXL"}, None)
    tk = await b._r_trades({"symbol": "SOXL", "count": 50}, None)
    return pr, tk
pr, tk = asyncio.run(run())
check("브릿지 /prices: 이상 현재가 50.69 → 매수1호가 52.70", pr and pr[0]["lastPrice"] == "52.7000", str(pr))
check("브릿지 /trades: 이상 틱 제거, 정상 틱 유지", [t["price"] for t in tk] == ["52.7100"], str([t["price"] for t in tk]))
print("결과(통합 포함):", "모두 통과" if ok else "실패 있음")
