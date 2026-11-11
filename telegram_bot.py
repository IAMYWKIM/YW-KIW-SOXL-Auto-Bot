# -*- coding: utf-8 -*-
"""
kiw_soxl_trader - 텔레그램 버튼 메뉴 (계좌현황 · 매매현황 · 성과보고 · 설정)

봇 토큰은 하나만 씁니다. 메시지 수신(폴링)은 원본 엔진이 하고, engine_hooks 가 엔진의
명령/버튼 처리 앞에 이 파일의 handle_text / handle_callback 을 끼워 넣습니다.
  · 여기서 아는 명령·버튼(callback_data 가 'kx:' 로 시작)이면 직접 처리하고 True 를 돌려줍니다.
  · 모르는 것은 False 를 돌려주어 원본 엔진 화면(레이더·지층 지시서·전술 제어반 등)이 그대로 동작합니다.
"""
import asyncio
import re
import time
from datetime import datetime, timedelta

import config
import emailer
import engine_hooks
import export
import notify
import reports
import scheduler
import settings_menu
import store
import trade_context
import version_history

MENU_TEXTS = ("📋 키움 메뉴", "📋 메뉴", "/menu", "/kmenu")
PERF_TEXTS = ("📈 성과보고", "/perf")
_started = time.time()


def strip_html(t: str) -> str:
    return re.sub(r"<[^>]+>", "", t or "")


def _kb(rows):
    return {"inline_keyboard": [[{"text": t, "callback_data": d[:64]} for t, d in row] for row in rows]}


def _usd(v, sign=False) -> str:
    v = float(v or 0)
    return f"{'+' if sign and v > 0 else ''}${v:,.2f}" if v >= 0 else f"-${abs(v):,.2f}"


def _mode_badge() -> str:
    if config.DRY_RUN:
        return "🧪 DRY_RUN(가상체결)"
    return "🟡 모의투자" if config.KIWOOM_IS_MOCK else "🔴 실전투자"


def _authorized(chat_id: str) -> bool:
    return not config.TELEGRAM_CHAT_ID or str(chat_id) == str(config.TELEGRAM_CHAT_ID)


def extra_reply_keyboard_row() -> list:
    return [{"text": "📋 키움 메뉴"}, {"text": "📈 성과보고"}]


# ══ 화면 ══════════════════════════════════════════════════════════════
def menu_view():
    pos = trade_context.position_summary()
    text = (f"📋 <b>키움 SOXL 스나이퍼 메뉴</b> · {_mode_badge()}\n"
            f"엔진 {'🟢 작동 중' if pos['is_active'] else '🔴 정지'} · 보유 {pos['qty']:,}주 {pos['tiers']}층 · "
            f"오늘 실현 {_usd(pos['realized_today'], True)}\n"
            f"<i>kiw_soxl_trader v{version_history.CURRENT_VERSION}</i>")
    rows = [
        [("📊 통합현황", "kx:status"), ("💼 계좌잔고", "kx:balance")],
        [("📦 보유지층", "kx:tiers"), ("🧾 오늘매매", "kx:today")],
        [("⏳ 미체결·주문", "kx:orders"), ("📜 운영이력", "kx:events")],
        [("📈 성과보고", "kx:perf"), ("📧 이메일보고", "kx:email")],
        [("🔫 레이더", "kx:eng:/avwap"), ("📜 지층 지시서", "kx:eng:/sync")],
        [("⚙️ 전술 제어반", "kx:eng:/settlement"), ("⚖️ 바벨", "kx:eng:/barbell"), ("🗡 암살자", "kx:eng:/assassin")],
        [("🛠 설정", "kx:set"), ("📡 API 부하", "kx:load"), ("ℹ️ 버전", "kx:ver")],
    ]
    return text, rows


def _back(refresh: str):
    return [[("🔄 새로고침", refresh), ("⬅️ 메뉴", "kx:menu")]]


async def status_view():
    pos = trade_context.position_summary()
    ctx = engine_hooks.runtime.context
    br = engine_hooks.runtime.bridge
    price = 0.0
    try:
        price = float(ctx.latest_prices.get("SOXL") or 0.0)
    except Exception:
        pass
    eval_amt = pos["qty"] * price
    pnl = eval_amt - pos["invested"]
    pct = pnl / pos["invested"] * 100 if pos["invested"] else 0.0
    L = [f"📊 <b>통합현황</b> · {_mode_badge()}",
         f"🕐 {datetime.now(store.KST):%m-%d %H:%M} KST / {datetime.now(store.ET):%H:%M} ET · {store.session_of()}",
         f"엔진: {'🟢 작동 중' if pos['is_active'] else '🔴 정지'} · 모드 {pos['portion_mode'] or '-'}", "",
         f"💹 SOXL 현재가 <b>${price:.2f}</b>",
         f"📦 보유 {pos['qty']:,}주 · {pos['tiers']}층" + (f" (+서브 {pos['sub_tiers']})" if pos["sub_tiers"] else "")
         + f" · 평단 ${pos['avg']:.2f}",
         f"   매수원금 {_usd(pos['invested'])} → 평가 {_usd(eval_amt)} ({_usd(pnl, True)}, {pct:+.2f}%)",
         f"🏦 봇 가용현금 {_usd(pos['free_cash'])} · 기준 시드 {_usd(pos['seed'])}",
         f"💰 실현손익: 오늘 {_usd(pos['realized_today'], True)} · 누적 {_usd(pos['realized_cum'], True)}"]
    st = pos["state"]
    if float(st.get("injected_reserve_usd") or 0) > 0 or st.get("waiting_reserve_deposit"):
        L.append(f"💉 예비시드 수혈 잔액 {_usd(st.get('injected_reserve_usd'))}"
                 + (" · ⏳ 입금 대기 중" if st.get("waiting_reserve_deposit") else ""))
    try:
        cash = await br.broker_cash()
        cap = float(config.get("SAFETY.MAX_BOT_BUYING_POWER_USD", 0) or 0)
        L.append("")
        L.append(f"💵 {'가상 ' if config.DRY_RUN else '키움 '}달러 예수금 {_usd(cash.get('cash'))} · 주문가능 {_usd(cash.get('orderable'))}"
                 + (f" (봇 상한 {_usd(cap)})" if cap else ""))
    except Exception as e:
        L.append(f"\n⚠️ 예수금 조회 실패: {str(e)[:80]}")
    opens = store.open_orders()
    L.append(f"⏳ 미체결 주문 {len(opens)}건 · 가동 {int((time.time() - _started) / 3600)}시간째")
    if br and br.last_error:
        L.append(f"⚠️ 최근 API 오류: {br.last_error[:120]}")
    return "\n".join(L), _back("kx:status")


async def balance_view():
    br = engine_hooks.runtime.bridge
    pos = trade_context.position_summary()
    L = [f"💼 <b>계좌잔고</b> ({'가상 계좌' if config.DRY_RUN else '키움 실계좌 조회'})"]
    try:
        h = await br.broker_holdings()
        cash = await br.broker_cash()
        if not h["items"]:
            L.append("보유 종목 없음")
        for sym, it in h["items"].items():
            L.append(f"• <b>{sym}</b> {it['qty']:,}주 (매도가능 {it['sellable']:,}) 평단 ${it['avg_price']:.2f} → ${it['last_price']:.2f}"
                     f"\n   매입 {_usd(it['purchase_amount'])} 평가 {_usd(it['eval_amount'])} ({_usd(it['eval_amount'] - it['purchase_amount'], True)})")
        L.append("")
        L.append(f"💵 달러 예수금 {_usd(cash.get('cash'))}\n   주문가능 {_usd(cash.get('orderable'))} · 출금가능 {_usd(cash.get('withdrawable'))}")
        b_qty = int(h["items"].get("SOXL", {}).get("qty", 0))
        if b_qty != pos["qty"]:
            L.append(f"\n⚠️ 계좌 SOXL {b_qty:,}주 ≠ 봇 지층 합계 {pos['qty']:,}주\n"
                     f"   (체결 직후 잠깐 다를 수 있습니다. 계속 다르면 📜 지층 지시서로 동기화)")
        else:
            L.append(f"\n✅ 계좌 SOXL 수량과 봇 지층 합계가 같습니다 ({b_qty:,}주)")
        unalloc = float(cash.get("orderable") or 0) - pos["free_cash"]
        L.append(f"🧮 봇 가용현금 {_usd(pos['free_cash'])} · 봇 몫이 아닌 주문가능 달러 {_usd(unalloc)}")
    except Exception as e:
        L.append(f"⚠️ 조회 실패: {str(e)[:200]}")
    return "\n".join(L), _back("kx:balance")


def tiers_view():
    pos = trade_context.position_summary()
    ctx = engine_hooks.runtime.context
    price = 0.0
    try:
        price = float(ctx.latest_prices.get("SOXL") or 0.0)
    except Exception:
        pass
    L = [f"📦 <b>보유 지층</b> · 현재가 ${price:.2f}"]
    if not pos["buckets"]:
        L.append("보유 지층이 없습니다 (0주).")
    for b in pos["buckets"]:
        diff = (price / b["price"] - 1) * 100 if b["price"] and price else 0.0
        L.append(f"{b['tier']}층 {b['qty']:>4,}주 @ ${b['price']:.2f} ({diff:+.2f}%) 원금 {_usd(b['cost'])}"
                 + (f" · {b['type']}" if b["type"] else ""))
    st = pos["state"]
    L.append("")
    L.append(f"합계 {pos['qty']:,}주 · 평단 ${pos['avg']:.2f} · 원금 {_usd(pos['invested'])}")
    L.append(f"기준가 ${float(st.get('anchor_price') or 0):.2f} · 기억저가 ${float(st.get('cycle_min_buy_price') or 0):.2f}")
    L.append("<i>익절 목표가·매수 게이트는 🔫 레이더(원본 화면)에서 확인하세요.</i>")
    return "\n".join(L), [[("🔄 새로고침", "kx:tiers"), ("🔫 레이더", "kx:eng:/avwap"), ("⬅️ 메뉴", "kx:menu")]]


def today_view(d: str = None):
    d = d or store.trade_date_today()
    fills = store.fills_between(d, d)
    lev = [e for e in store.ledger_between(d, d) if e["kind"] not in ("BASELINE",)]
    L = [f"🧾 <b>매매 내역</b> · 거래일 {d} (ET)"]
    if not fills:
        L.append("체결 없음")
    for f in fills[-25:]:
        L.append(f"{f['et'][11:16]} {'🛒매수' if f['side'] == 'BUY' else '💵매도'} {f['qty']:,}주 @ ${f['price']:.2f}"
                 f" ({f['session']}{' · ' + f['lot_type'] if f['lot_type'] else ''})")
    realized = sum(e["realized_net"] for e in lev if e["kind"] in ("SELL", "SUB_SELL"))
    L.append("")
    L.append(f"💰 실현손익 {_usd(realized, True)} · 매수 {sum(1 for f in fills if f['side'] == 'BUY')}건"
             f" · 매도 {sum(1 for f in fills if f['side'] == 'SELL')}건")
    extra = [e for e in lev if e["kind"] in ("CYCLE_DONE", "RESERVE_IN", "RESERVE_REPAY", "UNLOCK", "MODE", "ACTIVE", "SEED")]
    for e in extra[-8:]:
        L.append(f"  · {e['et'][11:16]} {export.KIND_KO.get(e['kind'], e['kind'])}")
    prev = store.prev_trade_date(d)
    return "\n".join(L), [[("◀️ 전 거래일", f"kx:today:{prev}"), ("🔄 오늘", "kx:today"), ("⬅️ 메뉴", "kx:menu")]]


def orders_view():
    opens = store.open_orders()
    L = ["⏳ <b>미체결 주문</b>"]
    if not opens:
        L.append("없음")
    for o in opens:
        L.append(f"• {'매수' if o['side'] == 'BUY' else '매도'} {o['qty']:,}주 @ ${o['price']:.2f} · {o['status']}"
                 f" · {o['placed_kst'][5:16]} · #{o['ord_no']}")
    L.append("")
    L.append("🗂 <b>최근 주문 10건</b>")
    for o in store.recent_orders(10):
        L.append(f"{o['placed_kst'][5:16]} {'매수' if o['side'] == 'BUY' else '매도'} {o['qty']:,}@{o['price']:.2f}"
                 f" → {o['status']}" + (f" ({o['filled_qty']}주 @{o['avg_fill_price']:.2f})" if o["filled_qty"] else ""))
    return "\n".join(L), _back("kx:orders")


def events_view():
    rows = store.query("SELECT * FROM events ORDER BY ts DESC LIMIT 15")
    L = ["📜 <b>운영이력 (최근 15건)</b>"]
    icon = {"API_ERROR": "⚠️", "ENGINE_ALERT": "🔔", "SETTING": "🛠", "REPORT": "📊", "HEALTH": "🩺", "SYSTEM": "⚙️"}
    for e in rows:
        L.append(f"{e['kst'][5:16]} {icon.get(e['kind'], '•')} {strip_html(e['title'])[:70]}")
    if not rows:
        L.append("기록 없음")
    return "\n".join(L), _back("kx:events")


def perf_view():
    text = ("📈 <b>성과보고</b>\n기간을 고르면 요약을 보여 줍니다. 📎 는 매매 복기용 엑셀을 보냅니다.\n"
            "<i>자동 발송: 일간(화~토 아침) · 주간(토) · 월간(1일) — 🛠 설정 → 🗓 보고</i>")
    rows = [[("오늘", "kx:rep:day:0"), ("전 거래일", "kx:rep:day:1"), ("📎 엑셀", "kx:xls:day:1")],
            [("이번 주", "kx:rep:week:0"), ("지난 주", "kx:rep:week:1"), ("📎 엑셀", "kx:xls:week:0")],
            [("이번 달", "kx:rep:month:0"), ("지난 달", "kx:rep:month:1"), ("📎 엑셀", "kx:xls:month:0")],
            [("📧 이메일로 보내기", "kx:email"), ("⬅️ 메뉴", "kx:menu")]]
    return text, rows


def _ref(kind: str, back: int) -> str:
    today = store.trade_date_today()
    if back == 0:
        return today
    d = datetime.strptime(today, "%Y-%m-%d")
    if kind == "day":
        return store.prev_trade_date(today)
    if kind == "week":
        return store.prev_trade_date((d - timedelta(days=d.weekday())).strftime("%Y-%m-%d"))
    return (d.replace(day=1) - timedelta(days=1)).strftime("%Y-%m-%d")


def email_view(note: str = ""):
    on = lambda k: "🟢" if config.get(k) else "🔴"  # noqa: E731
    acc = f"보내는 계정 {config.SMTP_USER} → 받는 사람 {', '.join(config.EMAIL_TO)}" if emailer.configured() \
        else "⚠️ 이메일 계정 미설정 (.env 의 SMTP_USER / SMTP_PASSWORD / EMAIL_TO)"
    text = (f"📧 <b>이메일 보고</b>\n{acc}\n\n자동발송 {on('EMAIL.ENABLED')} · 일간 {on('EMAIL.DAILY')} · 주간 {on('EMAIL.WEEKLY')}"
            f" · 월간 {on('EMAIL.MONTHLY')} · 엑셀 첨부 {on('EMAIL.ATTACH_EXCEL')}" + (f"\n\n{note}" if note else ""))
    rows = [[(f"{on('EMAIL.ENABLED')} 자동발송(전체)", "kx:em:ENABLED")],
            [(f"{on('EMAIL.DAILY')} 일간", "kx:em:DAILY"), (f"{on('EMAIL.WEEKLY')} 주간", "kx:em:WEEKLY"),
             (f"{on('EMAIL.MONTHLY')} 월간", "kx:em:MONTHLY")],
            [(f"{on('EMAIL.ATTACH_EXCEL')} 엑셀 첨부", "kx:em:ATTACH_EXCEL"), ("✉️ 테스트 메일", "kx:emtest")],
            [("📤 전 거래일 보고", "kx:emsend:day"), ("📤 이번 주", "kx:emsend:week"), ("📤 이번 달", "kx:emsend:month")],
            [("⬅️ 메뉴", "kx:menu")]]
    return text, rows


def load_view():
    br = engine_hooks.runtime.bridge
    if not br:
        return "브릿지 미연결", _back("kx:load")
    s = br.kiw.load_stats()
    L = ["📡 <b>키움 API 부하</b>",
         f"최근 1분 {s['per_min']}회 (5분 평균 {s['per_min_5']:.0f}회/분 · 오늘 최고 {s['peak_per_min']}회/분)",
         f"설정상 최대 {s['max_per_min']}회/분 · 사용률 {s['busy_pct']:.0f}%",
         f"평균 응답 {s['avg_latency'] * 1000:.0f}ms · 평균 대기 {s['avg_wait'] * 1000:.0f}ms",
         f"오늘 호출 {s['today']:,}회 · 오류 {s['errors']}회 · 브릿지 요청 {br.stats['req']:,} / 주문 {br.stats['orders']}",
         "", "TR별: " + ", ".join(f"{k} {v}" for k, v in s["top"])]
    age = br.newest_candle_age_min("SOXL")
    if age is not None:
        L.append(f"최신 1분봉: {age:+.1f}분 전 · 분봉 시간대 {'KST' if br._tz() is store.KST else 'ET'}")
    if br.last_error:
        L.append(f"최근 오류: {br.last_error[:200]}")
    return "\n".join(L), _back("kx:load")


def version_view():
    L = [f"ℹ️ <b>kiw_soxl_trader v{version_history.CURRENT_VERSION}</b>", config.BOT_LABEL,
         f"엔진 원본: {version_history.engine_fingerprint()}", f"실행 폴더: run/{engine_hooks.runtime.mode}", ""]
    for v, d, desc in version_history.HISTORY[:4]:
        L.append(f"<b>v{v}</b> ({d})\n{desc[:500]}")
    return "\n".join(L), [[("⬅️ 메뉴", "kx:menu")]]


# ══ 수신 처리 ═════════════════════════════════════════════════════════
async def _show(ctrl, chat_id, msg_id, view):
    text, rows = view
    await ctrl.edit_or_send_message(chat_id=chat_id, message_id=msg_id, text=text, reply_markup=_kb(rows))


async def handle_text(ctrl, chat_id: str, text: str) -> bool:
    t = text.strip()
    if not t or not _authorized(chat_id):
        return False
    first = t.split()[0].lower().split("@", 1)[0]
    args = t.split()[1:]
    send = lambda view: ctrl.send_message(text=view[0], chat_id=chat_id, reply_markup=_kb(view[1]))  # noqa: E731
    if t in MENU_TEXTS or first in ("/menu", "/kmenu"):
        await send(menu_view())
    elif t in PERF_TEXTS or first == "/perf":
        if args and args[0] in ("day", "week", "month"):
            ref = args[1] if len(args) > 1 else None
            await ctrl.send_message(text=reports.format_text(args[0], ref, fx=scheduler._fx()), chat_id=chat_id)
        else:
            await send(perf_view())
    elif first == "/account":
        await send(await balance_view())
    elif first == "/kstatus":
        await send(await status_view())
    elif first == "/tiers":
        await send(tiers_view())
    elif first == "/today":
        await send(today_view(args[0] if args else None))
    elif first == "/orders":
        await send(orders_view())
    elif first == "/events":
        await send(events_view())
    elif first == "/load":
        await send(load_view())
    elif first == "/version":
        await send(version_view())
    elif first == "/settings":
        await send(settings_menu.main_view())
    elif first == "/set":
        if len(args) >= 2:
            msg = settings_menu.set_by_text(args[0], " ".join(args[1:]))
            if args[0].startswith("FEE."):
                msg += "\n" + await apply_fee_to_engine()
            await ctrl.send_message(text=msg, chat_id=chat_id)
        else:
            await ctrl.send_message(text=settings_menu.all_keys_text(), chat_id=chat_id)
    elif first == "/report":
        kind = args[0] if args and args[0] in ("day", "week", "month") else "day"
        ref = args[1] if len(args) > 1 else _ref(kind, 1 if kind == "day" else 0)
        asyncio.create_task(scheduler.send_report(kind, ref, manual=True))
    elif first == "/export":
        kind = args[0] if args and args[0] in ("day", "week", "month") else "day"
        asyncio.create_task(_send_excel(kind, args[1] if len(args) > 1 else _ref(kind, 0)))
    elif first == "/email":
        if args and args[0] in ("on", "off"):
            config.set_value("EMAIL.ENABLED", args[0] == "on")
        await send(email_view())
    elif first == "/dryreset":
        br = engine_hooks.runtime.bridge
        if not config.DRY_RUN:
            await ctrl.send_message(text="DRY_RUN 모드에서만 쓸 수 있습니다.", chat_id=chat_id)
        elif args[:1] != ["confirm"]:
            await ctrl.send_message(text="가상 계좌를 처음 금액으로 되돌립니다. 엔진 지층(원장)은 그대로이므로 "
                                         "지층이 0주일 때만 쓰세요.\n실행: <code>/dryreset confirm</code>", chat_id=chat_id)
        else:
            br.paper_reset()
            await ctrl.send_message(text="🧪 가상 계좌를 초기화했습니다.", chat_id=chat_id)
    else:
        return False
    return True


async def _send_excel(kind: str, ref: str):
    try:
        path = await asyncio.to_thread(export.build_excel, kind, ref)
        await asyncio.to_thread(notify.send_document, path, f"📎 {reports.period_range(kind, ref)[2]} 엑셀")
    except Exception as e:
        await asyncio.to_thread(notify.send, f"⚠️ 엑셀 생성 실패: {e}")


async def apply_fee_to_engine() -> str:
    """수수료율을 바꾸면 엔진에도 곧바로 반영 (부팅 때 엔진이 하는 절차와 동일)"""
    ctx = engine_hooks.runtime.context
    if ctx is None:
        return "ℹ️ 엔진이 아직 준비되지 않아 다음 기동 때 반영됩니다."
    try:
        us_c, rt_p = await ctx.api.ensure_us_commission_rate(force_refresh=True)
        await ctx.ledger.update_state("SOXL", us_commission_rate=us_c, round_trip_fee_rate_pct=rt_p)
        try:
            await ctx.assassin_ledger.update_state(us_commission_rate=us_c, round_trip_fee_rate_pct=rt_p)
        except Exception:
            pass
        return f"🏦 엔진 수수료율 반영: 편도 {us_c * 100:.3f}% / 왕복 {rt_p:.2f}%"
    except Exception as e:
        return f"⚠️ 엔진 반영 실패(재시작하면 반영됩니다): {e}"


async def handle_callback(ctrl, cb: dict) -> bool:
    data = str(cb.get("data") or "")
    if not data.startswith("kx:"):
        return False
    msg = cb.get("message") if isinstance(cb.get("message"), dict) else {}
    chat_id = str((msg.get("chat") or {}).get("id") or "")
    msg_id = int(msg.get("message_id") or 0)
    if cb.get("id"):
        await ctrl._call_tg("answerCallbackQuery", {"callback_query_id": str(cb["id"])}, timeout_sec=5.0)
    if not chat_id or not _authorized(chat_id):
        return True
    p = data.split(":")
    act = p[1] if len(p) > 1 else ""
    if act == "menu":
        await _show(ctrl, chat_id, msg_id, menu_view())
    elif act == "status":
        await _show(ctrl, chat_id, msg_id, await status_view())
    elif act == "balance":
        await _show(ctrl, chat_id, msg_id, await balance_view())
    elif act == "tiers":
        await _show(ctrl, chat_id, msg_id, tiers_view())
    elif act == "today":
        await _show(ctrl, chat_id, msg_id, today_view(p[2] if len(p) > 2 else None))
    elif act == "orders":
        await _show(ctrl, chat_id, msg_id, orders_view())
    elif act == "events":
        await _show(ctrl, chat_id, msg_id, events_view())
    elif act == "perf":
        await _show(ctrl, chat_id, msg_id, perf_view())
    elif act == "load":
        await _show(ctrl, chat_id, msg_id, load_view())
    elif act == "ver":
        await _show(ctrl, chat_id, msg_id, version_view())
    elif act == "eng":                                   # 원본 엔진 화면으로 넘기기
        await ctrl._handle_command_or_text(chat_id=chat_id, raw_text=":".join(p[2:]))
    elif act == "rep":
        kind, ref = p[2], _ref(p[2], int(p[3]))
        text = reports.format_text(kind, ref, fx=scheduler._fx())
        await _show(ctrl, chat_id, msg_id, (text, [[("📎 엑셀 받기", f"kx:xls:{kind}:{p[3]}"), ("⬅️ 성과보고", "kx:perf")]]))
    elif act == "xls":
        asyncio.create_task(_send_excel(p[2], _ref(p[2], int(p[3]))))
    elif act == "email":
        await _show(ctrl, chat_id, msg_id, email_view())
    elif act == "em":
        config.set_value(f"EMAIL.{p[2]}", not bool(config.get(f"EMAIL.{p[2]}")))
        await _show(ctrl, chat_id, msg_id, email_view())
    elif act == "emtest":
        ok, m = await asyncio.to_thread(emailer.send, "[SOXL 스나이퍼] 테스트 메일",
                                        "키움 SOXL 스나이퍼 봇의 이메일 설정이 정상입니다.")
        await _show(ctrl, chat_id, msg_id, email_view(("✅ " if ok else "⚠️ ") + m))
    elif act == "emsend":
        kind = p[2]
        asyncio.create_task(scheduler.send_report(kind, _ref(kind, 1 if kind == "day" else 0), manual=True, to_email=True))
        await _show(ctrl, chat_id, msg_id, email_view("📤 보고를 만들어 텔레그램과 이메일로 보냅니다..."))
    elif act == "set":
        await _show(ctrl, chat_id, msg_id, settings_menu.section_view(p[2]) if len(p) > 2 else settings_menu.main_view())
    elif act in ("tog", "val"):
        key = settings_menu.toggle(p[2], int(p[3])) if act == "tog" else settings_menu.choose(p[2], int(p[3]), int(p[4]))
        text, rows = settings_menu.section_view(p[2])
        if key.startswith("FEE."):
            text += "\n\n" + await apply_fee_to_engine()
        await _show(ctrl, chat_id, msg_id, (text, rows))
    return True
