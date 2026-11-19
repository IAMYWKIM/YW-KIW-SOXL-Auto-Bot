# -*- coding: utf-8 -*-
"""
kiw_soxl_trader - 일간/주간/월간 성과 요약

거래일 기준은 미국 동부 날짜입니다 (한 번의 미국 장 = 한 거래일).
숫자 출처
  · 체결 수량·금액·수수료 : fills (브릿지가 키움 체결을 확인한 순간 기록)
  · 실현손익·싸이클 졸업·예비시드 : ledger_ev (원본 엔진 원장의 증가분)
  · 자산 추이 : snapshots
"""
from datetime import datetime, timedelta

import config
import store
import trade_context

WD = "월화수목금토일"


def period_range(kind: str, ref: str = None):
    """kind: day/week/month. ref: 기준 거래일(YYYY-MM-DD, 기본=오늘 ET). 반환 (시작, 끝, 표시이름, 파일꼬리표)"""
    d = datetime.strptime(ref or store.trade_date_today(), "%Y-%m-%d")
    if kind == "week":
        mon = d - timedelta(days=d.weekday())
        fri = mon + timedelta(days=4)
        iso = mon.isocalendar()
        sun = mon + timedelta(days=6)   # 집계는 월~일(주말에 찍힌 기록도 포함), 표시는 월~금
        return (mon.strftime("%Y-%m-%d"), sun.strftime("%Y-%m-%d"), f"주간 {iso[0]}-W{iso[1]:02d}",
                f"주간보고_{iso[0]}-W{iso[1]:02d}({mon:%m%d}-{fri:%m%d})")
    if kind == "month":
        first = d.replace(day=1)
        last = (first.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        today = datetime.strptime(store.trade_date_today(), "%Y-%m-%d")
        tail = f"월간보고_{d:%Y-%m}" + (f"_{today.month}월{today.day}일까지" if last > today >= first else "")
        return first.strftime("%Y-%m-%d"), last.strftime("%Y-%m-%d"), f"월간 {d:%Y-%m}", tail
    return (d.strftime("%Y-%m-%d"), d.strftime("%Y-%m-%d"), f"일간 {d:%Y-%m-%d}({WD[d.weekday()]})",
            f"일간보고_{d:%Y%m%d}({WD[d.weekday()]})")


def last_completed(kind: str) -> str:
    """자동 보고용 기준일: 방금 끝난 거래일 / 지난 주 / 지난 달"""
    today = store.trade_date_today()
    now_et = datetime.now(store.ET)
    d = today if (now_et.weekday() < 5 and now_et.hour >= 16) else store.prev_trade_date(today)
    if kind == "month":
        first = datetime.strptime(today, "%Y-%m-%d").replace(day=1)
        return (first - timedelta(days=1)).strftime("%Y-%m-%d") if now_et.day <= 3 else d
    return d


def build(d1: str, d2: str) -> dict:
    fills = store.fills_between(d1, d2)
    lev = store.ledger_between(d1, d2)
    snaps = store.snapshots_between(d1, d2)
    buys = [f for f in fills if f["side"] == "BUY"]
    sells = [f for f in fills if f["side"] == "SELL"]
    sell_ev = [e for e in lev if e["kind"] in ("SELL", "SUB_SELL")]
    realized = sum(e["realized_net"] for e in sell_ev)
    wins = sum(1 for e in sell_ev if e["realized_net"] > 0)
    days = {}
    for f in fills:
        r = days.setdefault(f["trade_date"], {"buy_n": 0, "sell_n": 0, "buy_amt": 0.0, "sell_amt": 0.0, "fee": 0.0,
                                              "realized": 0.0, "cycles": 0, "equity": None})
        k = "buy" if f["side"] == "BUY" else "sell"
        r[f"{k}_n"] += 1
        r[f"{k}_amt"] += f["amount"]
        r["fee"] += f["fee"]
    for e in lev:
        r = days.setdefault(e["trade_date"], {"buy_n": 0, "sell_n": 0, "buy_amt": 0.0, "sell_amt": 0.0, "fee": 0.0,
                                              "realized": 0.0, "cycles": 0, "equity": None})
        if e["kind"] in ("SELL", "SUB_SELL"):
            r["realized"] += e["realized_net"]
        if e["kind"] == "CYCLE_DONE":
            r["cycles"] += 1
    for s in snaps:
        if s["trade_date"] in days or True:
            days.setdefault(s["trade_date"], {"buy_n": 0, "sell_n": 0, "buy_amt": 0.0, "sell_amt": 0.0, "fee": 0.0,
                                              "realized": 0.0, "cycles": 0, "equity": None})["equity"] = s["equity"]
    by_session, by_type = {}, {}
    for f in fills:
        a = by_session.setdefault(f["session"], {"buy": 0, "sell": 0, "amt": 0.0})
        a["buy" if f["side"] == "BUY" else "sell"] += 1
        a["amt"] += f["amount"]
        t = by_type.setdefault((f["side"], f["lot_type"] or "-"), {"n": 0, "qty": 0, "amt": 0.0})
        t["n"] += 1
        t["qty"] += f["qty"]
        t["amt"] += f["amount"]
    prev = store.last_snapshot(before_date=d1)
    start_eq = prev["equity"] if prev else (snaps[0]["equity"] if snaps else None)
    end_eq = snaps[-1]["equity"] if snaps else None
    slip = [abs(f["price"] - f["limit_price"]) for f in fills if f["limit_price"]]
    return {
        "d1": d1, "d2": d2, "fills": fills, "ledger": lev, "snapshots": snaps, "days": dict(sorted(days.items())),
        "buy_n": len(buys), "sell_n": len(sells), "buy_qty": sum(f["qty"] for f in buys),
        "sell_qty": sum(f["qty"] for f in sells), "buy_amt": sum(f["amount"] for f in buys),
        "sell_amt": sum(f["amount"] for f in sells), "fee": sum(f["fee"] for f in fills),
        "realized": realized, "sell_events": len(sell_ev), "wins": wins,
        "win_rate": (wins / len(sell_ev) * 100) if sell_ev else None,
        "cycles": sum(1 for e in lev if e["kind"] == "CYCLE_DONE"),
        "reserve_in": sum(e["realized_gross"] for e in lev if e["kind"] == "RESERVE_IN"),
        "reserve_repay": sum(e["realized_gross"] for e in lev if e["kind"] == "RESERVE_REPAY"),
        "unlocks": sum(1 for e in lev if e["kind"] == "UNLOCK"),
        "start_equity": start_eq, "end_equity": end_eq, "by_session": by_session, "by_type": by_type,
        "avg_slip": (sum(slip) / len(slip)) if slip else 0.0,
        "errors": len(store.events_between(d1, d2, ("API_ERROR",))),
        "dry_run": any(f["dry_run"] for f in fills) or config.DRY_RUN,
    }


def _usd(v, sign=False) -> str:
    if v is None:
        return "-"
    return f"{'+' if sign and v > 0 else ''}${v:,.2f}" if v >= 0 else f"-${abs(v):,.2f}"


def format_text(kind: str, ref: str = None, s: dict = None, fx: float = 0.0) -> str:
    d1, d2, label, _ = period_range(kind, ref)
    s = s or build(d1, d2)
    pos = trade_context.position_summary()
    L = [f"📊 <b>[SOXL 스나이퍼] {label} 성과</b>" + (" · 🧪 DRY_RUN" if s["dry_run"] else ""),
         f"기간(미국 거래일): {d1}" + (f" ~ {d2}" if d2 != d1 else ""), ""]
    krw = f" (약 {s['realized'] * fx:,.0f}원)" if fx > 100 else ""
    L.append(f"💰 실현손익(수수료 차감): <b>{_usd(s['realized'], True)}</b>{krw}")
    if s["win_rate"] is not None:
        L.append(f"🎯 익절 매도 {s['sell_events']}회 · 이익 {s['wins']}회 ({s['win_rate']:.0f}%) · 싸이클 졸업 {s['cycles']}회")
    L.append(f"🛒 매수 {s['buy_n']}건 {s['buy_qty']:,}주 {_usd(s['buy_amt'])}")
    L.append(f"💵 매도 {s['sell_n']}건 {s['sell_qty']:,}주 {_usd(s['sell_amt'])}")
    L.append(f"🧾 수수료 추정 {_usd(s['fee'])}")
    if s["start_equity"] is not None and s["end_equity"] is not None:
        ch = s["end_equity"] - s["start_equity"]
        pct = ch / s["start_equity"] * 100 if s["start_equity"] else 0.0
        L.append(f"📈 평가 총자산: {_usd(s['start_equity'])} → {_usd(s['end_equity'])} ({_usd(ch, True)}, {pct:+.2f}%)")
    if s["reserve_in"] or s["reserve_repay"]:
        L.append(f"💉 예비시드 수혈 {_usd(s['reserve_in'])} · 상환 {_usd(s['reserve_repay'])}")
    if s["unlocks"]:
        L.append(f"🔓 스텝업 해금 {s['unlocks']}회")
    if s["by_session"]:
        L.append("")
        L.append("⏱ 시간대별 체결: " + " · ".join(f"{k} 매수{v['buy']}/매도{v['sell']}" for k, v in s["by_session"].items()))
    if kind != "day" and s["days"]:
        L.append("")
        L.append("📅 일별")
        for d, r in s["days"].items():
            if r["buy_n"] or r["sell_n"] or r["realized"]:
                L.append(f"  {d[5:]} 매수{r['buy_n']} 매도{r['sell_n']} 실현 {_usd(r['realized'], True)}"
                         + (f" 졸업{r['cycles']}" if r["cycles"] else ""))
    L.append("")
    L.append(f"📦 현재 보유: {pos['qty']:,}주 · {pos['tiers']}층" + (f"(+서브 {pos['sub_tiers']})" if pos["sub_tiers"] else "")
             + f" · 평단 ${pos['avg']:.2f} · 매수원금 {_usd(pos['invested'])}")
    L.append(f"🏦 봇 가용현금 {_usd(pos['free_cash'])} · 누적 실현 {_usd(pos['realized_cum'], True)}")
    if s["errors"]:
        L.append(f"⚠️ 키움 API 오류 알림 {s['errors']}건 (📜 운영이력 참고)")
    if not s["fills"] and not s["ledger"]:
        L.append("ℹ️ 이 기간에는 체결이 없었습니다.")
    return "\n".join(L)
