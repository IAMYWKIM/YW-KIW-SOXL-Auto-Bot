# -*- coding: utf-8 -*-
"""
kiw_soxl_trader - 매매 복기용 상황 스냅샷

주문·체결 순간에 "왜 그 주문이 나갔는지" 나중에 되짚을 수 있도록
원본 엔진의 원장(ledger)과 지표 캐시에서 핵심 값만 뽑아 기록에 붙입니다.
엔진 내부 구조를 직접 건드리지 않고 읽기만 합니다 (값이 없으면 조용히 건너뜁니다).
"""
import engine_hooks

LEDGER_KEYS = (
    "is_active", "portion_mode", "initial_seed_usd", "free_cash_usd", "anchor_price", "cycle_min_buy_price",
    "pending_lot_type", "pending_sell_mode", "pending_sell_indices", "pending_portion_budget",
    "ordered_qty", "buy_price", "target_sell_price", "l1_target_quota_qty", "used_reserve_portions_cnt",
    "injected_reserve_usd", "waiting_reserve_deposit", "cumulative_realized_net_usd", "daily_realized_net_usd",
    "completed_main_cycles", "unlock_mode", "main_consec_frozen_days",
)


def _num(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def buckets_brief(buckets) -> list:
    out = []
    for i, b in enumerate(buckets or []):
        if isinstance(b, dict):
            out.append({"tier": i + 1, "qty": int(_num(b.get("qty"))), "price": round(_num(b.get("book_price")), 4),
                        "cost": round(_num(b.get("actual_cost_usd")), 2), "type": str(b.get("lot_type") or ""),
                        "lots": int(_num(b.get("lot_count"), 1))})
    return out


def ledger_state() -> dict:
    ctx = engine_hooks.runtime.context
    if ctx is None:
        return {}
    try:
        return ctx.ledger.get_state("SOXL") or {}
    except Exception:
        return {}


def snapshot() -> dict:
    """체결 기록(fills.context)·주문 기록(orders.context)에 붙는 요약"""
    ctx = engine_hooks.runtime.context
    if ctx is None:
        return {}
    out = {}
    st = ledger_state()
    for k in LEDGER_KEYS:
        if k in st:
            out[k] = st[k]
    bl = buckets_brief(st.get("buckets"))
    out["tiers"] = len(bl)
    out["buckets"] = bl
    sub = buckets_brief(st.get("sub_buckets"))
    if sub:
        out["sub_buckets"] = sub
    try:
        out["live_price"] = round(_num(ctx.latest_prices.get("SOXL")), 4)
        out["qqq_price"] = round(_num(ctx.latest_prices.get("QQQ")), 4)
    except Exception:
        pass
    try:
        m = (ctx.candle_metrics_cache or {}).get("SOXL") or {}
        ind = {}
        for k, v in m.items():
            if isinstance(v, bool) or isinstance(v, (int, float)):
                ind[k] = round(float(v), 4) if not isinstance(v, bool) else v
            elif isinstance(v, str) and len(v) <= 40:
                ind[k] = v
            if len(ind) >= 45:
                break
        out["indicators"] = ind
    except Exception:
        pass
    return out


def position_summary() -> dict:
    """현황 화면·스냅샷용: 지층 합계"""
    st = ledger_state()
    bl = buckets_brief(st.get("buckets")) + buckets_brief(st.get("sub_buckets"))
    qty = sum(b["qty"] for b in bl)
    cost = sum(b["cost"] for b in bl)
    return {"qty": qty, "invested": round(cost, 2), "avg": round(cost / qty, 4) if qty else 0.0,
            "tiers": len(buckets_brief(st.get("buckets"))), "sub_tiers": len(buckets_brief(st.get("sub_buckets"))),
            "free_cash": round(_num(st.get("free_cash_usd")), 2), "seed": round(_num(st.get("initial_seed_usd")), 2),
            "realized_cum": round(_num(st.get("cumulative_realized_net_usd")), 2),
            "realized_today": round(_num(st.get("daily_realized_net_usd")), 2),
            "is_active": bool(st.get("is_active", True)), "portion_mode": str(st.get("portion_mode") or ""),
            "buckets": bl, "state": st}
