# -*- coding: utf-8 -*-
"""
kiw_soxl_trader - 백그라운드 작업 (엔진과 같은 asyncio 루프에서 조용히 함께 돕니다)

  journal_loop  : 1초마다 엔진 원장을 읽어 변화(매수/매도/실현손익/졸업/예비시드/설정 변경)를 기록
  snapshot_loop : 미국 장 시간(04:00~20:00 ET)에 N분마다 계좌·지층 스냅샷
  report_loop   : 일간(화~토 KST) · 주간(토 KST) · 월간(매월 1일 KST) 보고를 텔레그램·이메일로 발송
  health_loop   : 분봉 지연 감시, 원장 사본 보관, API 오류 알림

이 파일의 어떤 오류도 매매 엔진을 멈추지 않도록 각 단계는 따로 감싸져 있습니다.
"""
import asyncio
import json
import time
from datetime import datetime

import config
import emailer
import engine_hooks
import export
import notify
import reports
import store
import trade_context
from logger import get_logger

log = get_logger("sched")

WATCH_NUM = ("total_buy_events", "total_buy_shares", "total_sell_events", "total_sold_shares",
             "cumulative_realized_net_usd", "cumulative_realized_gross_usd", "cumulative_realized_fee_usd",
             "sub_buy_events", "sub_buy_shares", "sub_sell_events", "sub_sold_shares", "sub_realized_net_usd",
             "sub_realized_gross_usd", "sub_realized_fee_usd", "completed_main_cycles",
             "total_reserve_injected_cnt", "injected_reserve_usd", "cumulative_reserve_repaid_usd",
             "cond_unlock_buy_events", "initial_seed_usd")
WATCH_FLAG = ("is_active", "portion_mode")


def _f(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _price() -> float:
    ctx = engine_hooks.runtime.context
    try:
        return round(_f(ctx.latest_prices.get("SOXL")), 4) if ctx else 0.0
    except Exception:
        return 0.0


def diff_ledger(prev: dict, cur: dict) -> list:
    """원장 두 시점의 차이 → 기록할 사건 목록 (순수 함수: 테스트 가능)"""
    ev = []
    d = {k: _f(cur.get(k)) - _f(prev.get(k)) for k in WATCH_NUM}
    if any(d[k] < -1e-9 for k in ("total_buy_events", "total_sell_events", "cumulative_realized_net_usd")) \
            and _f(cur.get("total_sell_events")) == 0:
        return [{"kind": "RESET", "detail": {"설명": "실현수익/통계 초기화(/reset_profit) 감지 — 기준점 재설정"}}]
    if d["total_buy_events"] > 0:
        ev.append({"kind": "BUY", "qty": int(d["total_buy_shares"]),
                   "detail": {"횟수": int(d["total_buy_events"]), "주문유형": prev.get("pending_lot_type") or cur.get("pending_lot_type")}})
    if d["cond_unlock_buy_events"] > 0:
        ev.append({"kind": "UNLOCK", "detail": {"횟수": int(d["cond_unlock_buy_events"])}})
    if d["total_sell_events"] > 0 or abs(d["cumulative_realized_net_usd"]) > 1e-9:
        ev.append({"kind": "SELL", "qty": int(d["total_sold_shares"]), "realized_net": d["cumulative_realized_net_usd"],
                   "realized_gross": d["cumulative_realized_gross_usd"], "fee": d["cumulative_realized_fee_usd"],
                   "detail": {"횟수": int(d["total_sell_events"]), "매도방식": prev.get("pending_sell_mode"),
                              "매도층": prev.get("pending_sell_indices")}})
    if d["sub_buy_events"] > 0:
        ev.append({"kind": "SUB_BUY", "qty": int(d["sub_buy_shares"]), "detail": {"횟수": int(d["sub_buy_events"])}})
    if d["sub_sell_events"] > 0 or abs(d["sub_realized_net_usd"]) > 1e-9:
        ev.append({"kind": "SUB_SELL", "qty": int(d["sub_sold_shares"]), "realized_net": d["sub_realized_net_usd"],
                   "realized_gross": d["sub_realized_gross_usd"], "fee": d["sub_realized_fee_usd"],
                   "detail": {"횟수": int(d["sub_sell_events"])}})
    if d["completed_main_cycles"] > 0:
        ev.append({"kind": "CYCLE_DONE", "detail": {"누적 졸업": int(_f(cur.get("completed_main_cycles"))),
                                                    "직전 싸이클 순익": cur.get("last_cycle_realized_net_usd")}})
    if d["total_reserve_injected_cnt"] > 0:
        ev.append({"kind": "RESERVE_IN", "realized_gross": max(0.0, d["injected_reserve_usd"]),
                   "detail": {"회분": int(d["total_reserve_injected_cnt"]), "수혈 잔액": cur.get("injected_reserve_usd")}})
    if d["cumulative_reserve_repaid_usd"] > 1e-9:
        ev.append({"kind": "RESERVE_REPAY", "realized_gross": d["cumulative_reserve_repaid_usd"], "detail": {}})
    if abs(d["initial_seed_usd"]) > 1e-9 and _f(prev.get("initial_seed_usd")) > 0:
        ev.append({"kind": "SEED", "detail": {"이전": prev.get("initial_seed_usd"), "이후": cur.get("initial_seed_usd")}})
    for k, kind in (("is_active", "ACTIVE"), ("portion_mode", "MODE")):
        if k in prev and prev.get(k) != cur.get(k):
            ev.append({"kind": kind, "detail": {"이전": prev.get(k), "이후": cur.get(k)}})
    return ev


async def journal_loop():
    key = f"ledger_watch:{engine_hooks.runtime.mode}"
    prev = store.kv_get(key)
    seen = {}                                     # 주문 대기 중에만 잠깐 보이는 값(주문유형·매도방식)을 기억
    while True:
        await asyncio.sleep(1.0)
        try:
            st = trade_context.ledger_state()
            if not st:
                continue
            for k in ("pending_lot_type", "pending_sell_mode", "pending_sell_indices"):
                if st.get(k):
                    seen[k] = st.get(k)
            cur = {k: st.get(k) for k in WATCH_NUM + WATCH_FLAG + ("pending_lot_type", "pending_sell_mode",
                                                                   "pending_sell_indices", "last_cycle_realized_net_usd")}
            if prev is None:
                prev = cur
                store.kv_set(key, cur)
                store.add_ledger_event("BASELINE", detail={"설명": "이 시점부터 원장 변화를 기록합니다", **{
                    k: cur.get(k) for k in ("total_buy_events", "total_sell_events", "cumulative_realized_net_usd")}})
                continue
            if all(prev.get(k) == cur.get(k) for k in WATCH_NUM + WATCH_FLAG):
                prev = cur
                continue
            pos = trade_context.position_summary()
            for e in diff_ledger({**prev, **{k: v for k, v in seen.items() if not prev.get(k)}}, cur):
                store.add_ledger_event(e["kind"], qty=e.get("qty", 0), realized_net=e.get("realized_net", 0.0),
                                       realized_gross=e.get("realized_gross", 0.0), fee=e.get("fee", 0.0),
                                       price=_price(), tiers=pos["tiers"], free_cash=pos["free_cash"],
                                       detail=e.get("detail"))
            prev = cur
            store.kv_set(key, cur)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.debug("원장 감시 일시 오류: %s", e)


async def take_snapshot() -> dict:
    pos = trade_context.position_summary()
    price = _price()
    broker_cash = 0.0
    br = engine_hooks.runtime.bridge
    try:
        broker_cash = float((await br.broker_cash()).get("cash") or 0.0) if br else 0.0
    except Exception:
        pass
    eval_amt = round(pos["qty"] * price, 2) if price else pos["invested"]
    equity = round(pos["free_cash"] + eval_amt, 2)
    st = pos["state"]
    detail = {"reserve": st.get("injected_reserve_usd"), "mode": st.get("portion_mode"), "active": st.get("is_active")}
    for nm in ("barbell_ledger", "assassin_ledger"):
        try:
            s = getattr(engine_hooks.runtime.context, nm).get_state()
            detail[nm] = {k: v for k, v in s.items() if isinstance(v, (int, float, str, bool)) and len(str(v)) < 40}
        except Exception:
            pass
    store.add_snapshot(price, pos["qty"], pos["avg"], pos["invested"], eval_amt, pos["free_cash"], equity,
                       pos["realized_cum"], pos["tiers"], broker_cash, detail)
    return {"equity": equity, "price": price}


async def snapshot_loop():
    await asyncio.sleep(20)
    last = 0.0
    while True:
        try:
            now_et = datetime.now(store.ET)
            hm = now_et.hour * 100 + now_et.minute
            interval = max(1, int(config.get("REPORT.SNAPSHOT_INTERVAL_MIN", 10) or 10)) * 60
            in_session = now_et.weekday() < 5 and 400 <= hm <= 2005
            now = asyncio.get_event_loop().time()
            if engine_hooks.runtime.context is not None and (last == 0.0 or (in_session and now - last >= interval)):
                await take_snapshot()
                last = now
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.debug("스냅샷 일시 오류: %s", e)
        await asyncio.sleep(30)


def _fx() -> float:
    ctx = engine_hooks.runtime.context
    try:
        return _f(ctx.last_fx_rate) if ctx else 0.0
    except Exception:
        return 0.0


async def send_report(kind: str, ref: str = None, manual: bool = False, to_email=None) -> str:
    """보고 1건 발송. to_email: None=자동 규칙, True/False=강제"""
    d1, d2, label, _ = reports.period_range(kind, ref)
    try:
        await take_snapshot()
    except Exception:
        pass
    s = reports.build(d1, d2)
    text = reports.format_text(kind, ref, s, fx=_fx())
    await asyncio.to_thread(notify.send, text)
    path = None
    want_mail = emailer.auto_enabled(kind) if to_email is None else bool(to_email)
    if config.get("REPORT.ATTACH_EXCEL_TELEGRAM", True) or want_mail or manual:
        try:
            path = await asyncio.to_thread(export.build_excel, kind, ref)
        except Exception as e:
            log.warning("엑셀 생성 실패: %s", e)
    if path and (config.get("REPORT.ATTACH_EXCEL_TELEGRAM", True) or manual):
        await asyncio.to_thread(notify.send_document, path, f"📎 {label} 엑셀 (매매 복기용)")
    result = "텔레그램 발송"
    if want_mail:
        attach = [path] if (path and config.get("EMAIL.ATTACH_EXCEL", True)) else []
        ok, msg = await asyncio.to_thread(emailer.send, f"[SOXL 스나이퍼] {label} 성과보고", text, attach)
        result += f" · 이메일 {msg}"
        if not ok:
            await asyncio.to_thread(notify.send, f"⚠️ 이메일 {msg}")
    store.add_event("REPORT", f"{label} 보고 발송", result)
    return result


async def report_loop():
    await asyncio.sleep(45)
    while True:
        try:
            now = datetime.now(store.KST)
            hm = now.strftime("%H:%M")
            wd = now.weekday()                       # 0=월 … 5=토
            jobs = []
            if config.get("REPORT.DAILY_ENABLED", True) and 1 <= wd <= 5 and hm >= str(config.get("REPORT.DAILY_TIME_KST", "07:10")):
                jobs.append(("day", f"day:{now:%Y%m%d}"))
            if config.get("REPORT.WEEKLY_ENABLED", True) and wd == 5 and hm >= str(config.get("REPORT.WEEKLY_TIME_KST", "07:20")):
                jobs.append(("week", f"week:{now:%Y%m%d}"))
            if config.get("REPORT.MONTHLY_ENABLED", True) and now.day == 1 and hm >= str(config.get("REPORT.MONTHLY_TIME_KST", "07:30")):
                jobs.append(("month", f"month:{now:%Y%m}"))
            for kind, key in jobs:
                if store.kv_get(f"report_sent:{key}"):
                    continue
                if now.hour >= 12:                   # 오전을 놓쳤으면(재기동 등) 그날은 건너뜀
                    store.kv_set(f"report_sent:{key}", "skipped")
                    continue
                if engine_hooks.runtime.context is None:
                    continue
                store.kv_set(f"report_sent:{key}", now.isoformat())
                ref = reports.last_completed(kind)
                if kind == "day" and not _was_us_trading_day(ref):
                    continue
                log.info("자동 보고 발송: %s (기준 %s)", kind, ref)
                await send_report(kind, ref)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning("보고 일정 처리 오류: %s", e)
        await asyncio.sleep(30)


def _was_us_trading_day(d: str) -> bool:
    """주말이 아니고, 그날 스냅샷이나 체결이 하나라도 있으면 거래일로 봅니다 (미국 휴장일은 기록이 없어 자동 생략)"""
    if datetime.strptime(d, "%Y-%m-%d").weekday() >= 5:
        return False
    return bool(store.snapshots_between(d, d) or store.fills_between(d, d))


async def health_loop():
    await asyncio.sleep(60)
    tick = 0
    while True:
        try:
            tick += 1
            if tick % 60 == 1:
                await asyncio.to_thread(engine_hooks.mirror_ledgers)
            br = engine_hooks.runtime.bridge
            now_et = datetime.now(store.ET)
            hm = now_et.hour * 100 + now_et.minute
            if br and now_et.weekday() < 5 and 935 <= hm <= 1555:
                age = br.newest_candle_age_min("SOXL")
                lim = float(config.get("ALERT.STALE_CANDLE_MIN", 5) or 5)
                if age is not None and abs(age) > lim:
                    msg = (f"⚠️ <b>[SOXL 분봉 시각 점검]</b>\n최신 1분봉이 현재 시각과 {age:+.0f}분 차이납니다.\n"
                           f"시세 지연이거나 분봉 시간대 설정(KIWOOM.CHART_TZ) 문제일 수 있습니다. "
                           f"<code>python3 diag.py</code> 로 확인하세요.")
                    if await asyncio.to_thread(notify.alert_once, "stale_candle", msg, 120):
                        store.add_event("HEALTH", "분봉 시각 차이", f"{age:+.1f}분")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.debug("상태 점검 일시 오류: %s", e)
        await asyncio.sleep(60)


async def startup_radar_once():
    """기동 직후 1회: 엔진의 🔫 스나이퍼 레이더 화면(/avwap)을 텔레그램으로 보냅니다.
    시세·1분봉 지표가 채워진 뒤에 보내야 내용이 비지 않으므로 최대 3분까지 기다립니다."""
    if not config.get("ALERT.STARTUP_RADAR", True) or not notify.enabled():
        return
    ctx = None
    for _ in range(180):
        await asyncio.sleep(1.0)
        ctx = engine_hooks.runtime.context
        if ctx is None or getattr(ctx, "tg", None) is None:
            continue
        try:
            ready = bool((ctx.candle_metrics_cache or {}).get("SOXL")) and float(ctx.latest_prices.get("SOXL") or 0) > 0
        except Exception:
            ready = False
        if ready:
            break
    if ctx is None:
        return
    await asyncio.sleep(2.0)                     # 생존 배너(✅ 서버가 정상 작동 중입니다) 다음 순서로 오도록
    chat_id = str(config.TELEGRAM_CHAT_ID)
    try:
        tg_mod = engine_hooks.runtime.modules.get("tg_ui_plugin")
        ctrl = tg_mod.TelegramCommandController(ctx) if tg_mod else None
        if ctrl is not None and hasattr(ctrl, "_render_and_send_avwap"):
            await ctrl._render_and_send_avwap(chat_id=chat_id)
        else:                                    # 원본 구조가 바뀐 경우: 명령을 그대로 흘려 보냄
            await ctx.tg._handle_command_or_text(chat_id=chat_id, raw_text="/avwap")
        log.info("기동 레이더 화면 발송 완료")
    except Exception as e:
        log.warning("기동 레이더 화면 발송 실패: %s", e)


def make_api_error_handler(loop):
    """브릿지에서 키움 오류가 나면 기록하고, 같은 오류는 쿨다운을 두고 알립니다."""
    recent = {}                                    # 일시 오류 발생 시각 (api_id → [ts...])

    def _handler(api_id: str, code: str, msg: str, transient: bool = False):
        try:
            store.add_event("API_ERROR", f"{api_id} {code}" + (" (일시)" if transient else ""), msg)
            if transient:
                # 키움 서버 일시 오류는 자동 재시도·직전 값으로 넘어가므로, 10분 안에 3번 이상 반복될 때만 알립니다
                now = time.time()
                lst = [t for t in recent.get(api_id, []) if now - t < 600] + [now]
                recent[api_id] = lst
                if len(lst) < int(config.get("ALERT.TRANSIENT_REPEAT", 3) or 3):
                    return
                msg = f"10분 안에 {len(lst)}회 반복 · " + msg
            if config.get("ALERT.API_ERROR", True) and code not in ("order-hours-closed",):
                cd = float(config.get("ALERT.API_ERROR_COOLDOWN_MIN", 30) or 30)
                text = f"⚠️ <b>[키움 API 오류]</b> {api_id}\n{code}\n{str(msg)[:300]}"
                loop.run_in_executor(None, notify.alert_once, f"apierr:{api_id}:{code}", text, cd)
        except Exception:
            pass
    return _handler


def make_fill_handler(loop):
    def _handler(symbol, side, qty, price, oid):
        if config.get("ALERT.FILL", False):
            text = f"{'🛒 매수' if side == 'BUY' else '💵 매도'} 체결 기록: {symbol} {qty}주 @ ${price:.2f}"
            loop.run_in_executor(None, notify.send, text)
    return _handler


def start_all() -> list:
    loop = asyncio.get_event_loop()
    br = engine_hooks.runtime.bridge
    if br is not None:
        br.on_api_error = make_api_error_handler(loop)
        br.on_fill = make_fill_handler(loop)
    return [asyncio.create_task(c(), name=c.__name__)
            for c in (journal_loop, snapshot_loop, report_loop, health_loop, startup_radar_once)]


def dumps(o) -> str:
    return json.dumps(o, ensure_ascii=False)
