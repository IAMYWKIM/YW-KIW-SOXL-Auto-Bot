# -*- coding: utf-8 -*-
"""
kiw_soxl_trader - 엑셀 보고서 (매매 복기용)

시트: 안내 · 요약 · 일별 · 체결내역(복기) · 주문내역 · 엔진원장변화 · 현재지층 · 자산추이 · 운영이력
파일명: kiw_soxl_일간보고_20261110(화).xlsx / 주간보고_2026-W46(1109-1113) / 월간보고_2026-11
"""
import json
from datetime import datetime

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

import config
import reports
import store
import trade_context
import version_history

HEAD_FILL = PatternFill("solid", fgColor="1F4E78")
HEAD_FONT = Font(bold=True, color="FFFFFF")
BUY_FILL = PatternFill("solid", fgColor="FDE9E7")
SELL_FILL = PatternFill("solid", fgColor="E7F0FD")
KIND_KO = {"BUY": "매수(본그리드)", "SELL": "매도(본그리드)", "SUB_BUY": "매수(서브)", "SUB_SELL": "매도(서브)",
           "CYCLE_DONE": "싸이클 졸업(0주)", "RESERVE_IN": "예비시드 수혈", "RESERVE_REPAY": "예비시드 상환",
           "UNLOCK": "스텝업 해금 매수", "ACTIVE": "작동 ON/OFF", "MODE": "운용 모드 변경", "SEED": "기준 시드 변경",
           "RESET": "실현수익 초기화", "BASELINE": "기록 시작"}


def _sheet(wb, title, headers, rows, widths=None, fills=None):
    ws = wb.create_sheet(title)
    ws.append(headers)
    for c in ws[1]:
        c.fill, c.font, c.alignment = HEAD_FILL, HEAD_FONT, Alignment(horizontal="center", vertical="center")
    for i, r in enumerate(rows):
        ws.append(r)
        if fills and fills[i] is not None:
            for c in ws[ws.max_row]:
                c.fill = fills[i]
    for i, h in enumerate(headers, 1):
        w = (widths or {}).get(h) or max(10, min(40, len(str(h)) * 2 + 4))
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = "A2"
    if rows:
        ws.auto_filter.ref = ws.dimensions
    return ws


def _ctx(raw) -> dict:
    try:
        return json.loads(raw or "{}")
    except Exception:
        return {}


def _ind(c: dict, *names):
    ind = c.get("indicators") or {}
    for n in names:
        for k, v in ind.items():
            if n in k.lower() and isinstance(v, (int, float)):
                return v
    return None


def build_excel(kind: str, ref: str = None):
    d1, d2, label, tail = reports.period_range(kind, ref)
    s = reports.build(d1, d2)
    wb = Workbook()
    ws = wb.active
    ws.title = "안내"
    info = [
        ("보고 종류", label), ("파일", f"kiw_soxl_{tail}.xlsx"), ("기간(미국 거래일)", f"{d1} ~ {d2}"),
        ("만든 시각(KST)", datetime.now(store.KST).strftime("%Y-%m-%d %H:%M")),
        ("봇", config.BOT_LABEL), ("버전", f"kiw_soxl_trader v{version_history.CURRENT_VERSION}"),
        ("운영 모드", ("모의(MOCK)" if config.KIWOOM_IS_MOCK else "실전(REAL)") + (" · DRY_RUN(가상체결)" if config.DRY_RUN else "")),
        ("", ""),
        ("체결내역(복기)", "체결 1건마다 그 순간의 현재가·지층 수·기준가·기억저가·가용현금·지표를 함께 적었습니다."),
        ("엔진원장변화", "원본 엔진이 스스로 정산한 실현손익·졸업·예비시드 변화입니다. 실현손익의 기준 자료입니다."),
        ("수수료", f"체결내역의 수수료는 설정한 편도 수수료율({config.get('FEE.US_COMMISSION_RATE', 0) * 100:.3f}%)로 계산한 추정값입니다. "
                "실제 청구액은 키움 거래내역으로 확인하세요."),
        ("시간", "ET=미국 동부시간, KST=한국시간. 거래일은 미국 동부 날짜 기준입니다."),
    ]
    for a, b in info:
        ws.append([a, b])
    ws.column_dimensions["A"].width = 22
    ws.column_dimensions["B"].width = 100
    for c in ws["A"]:
        c.font = Font(bold=True)

    pos = trade_context.position_summary()
    summ = [
        ("실현손익(수수료 차감, USD)", round(s["realized"], 2)), ("익절 매도 횟수", s["sell_events"]),
        ("이익 매도 횟수", s["wins"]), ("승률(%)", round(s["win_rate"], 1) if s["win_rate"] is not None else ""),
        ("싸이클 졸업 횟수", s["cycles"]), ("매수 체결 건수", s["buy_n"]), ("매수 수량", s["buy_qty"]),
        ("매수 금액(USD)", round(s["buy_amt"], 2)), ("매도 체결 건수", s["sell_n"]), ("매도 수량", s["sell_qty"]),
        ("매도 금액(USD)", round(s["sell_amt"], 2)), ("수수료 추정(USD)", round(s["fee"], 2)),
        ("기간 시작 평가 총자산", s["start_equity"] if s["start_equity"] is not None else ""),
        ("기간 끝 평가 총자산", s["end_equity"] if s["end_equity"] is not None else ""),
        ("예비시드 수혈(USD)", round(s["reserve_in"], 2)), ("예비시드 상환(USD)", round(s["reserve_repay"], 2)),
        ("스텝업 해금 매수", s["unlocks"]), ("지정가 대비 체결가 평균 차이(USD)", round(s["avg_slip"], 4)),
        ("키움 API 오류 알림", s["errors"]), ("", ""),
        ("[지금] 보유 수량", pos["qty"]), ("[지금] 본그리드 층수", pos["tiers"]), ("[지금] 서브 층수", pos["sub_tiers"]),
        ("[지금] 평단", pos["avg"]), ("[지금] 매수원금", pos["invested"]), ("[지금] 봇 가용현금", pos["free_cash"]),
        ("[지금] 기준 시드", pos["seed"]), ("[지금] 누적 실현손익", pos["realized_cum"]),
    ]
    _sheet(wb, "요약", ["항목", "값"], summ, {"항목": 38, "값": 20})
    _sheet(wb, "일별", ["거래일", "매수 건", "매도 건", "매수 금액", "매도 금액", "수수료 추정", "실현손익", "졸업", "평가 총자산(마지막)"],
           [[d, r["buy_n"], r["sell_n"], round(r["buy_amt"], 2), round(r["sell_amt"], 2), round(r["fee"], 2),
             round(r["realized"], 2), r["cycles"], r["equity"] if r["equity"] is not None else ""]
            for d, r in s["days"].items()], {"거래일": 13, "평가 총자산(마지막)": 20})

    rows, fills = [], []
    for f in s["fills"]:
        c = _ctx(f["context"])
        rows.append([f["trade_date"], f["et"][11:], f["kst"], f["session"], "매수" if f["side"] == "BUY" else "매도",
                     f["qty"], f["price"], f["limit_price"], f["amount"], f["fee"], f["lot_type"],
                     c.get("live_price"), c.get("tiers"), c.get("anchor_price"), c.get("cycle_min_buy_price"),
                     c.get("free_cash_usd"), c.get("target_sell_price"), _ind(c, "rsi"), _ind(c, "vwap"),
                     c.get("qqq_price"), " / ".join(f"{b['tier']}층 {b['qty']}주@{b['price']}" for b in c.get("buckets", [])),
                     f["order_id"], "가상" if f["dry_run"] else "", json.dumps(c.get("indicators") or {}, ensure_ascii=False)[:900]])
        fills.append(BUY_FILL if f["side"] == "BUY" else SELL_FILL)
    _sheet(wb, "체결내역(복기)",
           ["거래일", "체결시각(ET)", "체결시각(KST)", "시간대", "구분", "수량", "체결가", "주문 지정가", "체결금액", "수수료 추정",
            "엔진 주문유형", "당시 현재가", "당시 층수", "기준가(anchor)", "기억저가", "당시 가용현금", "목표 매도가", "RSI", "VWAP",
            "QQQ", "당시 지층", "주문ID", "비고", "당시 지표 전체"], rows,
           {"체결시각(KST)": 20, "엔진 주문유형": 24, "당시 지층": 60, "주문ID": 24, "당시 지표 전체": 60}, fills)

    _sheet(wb, "주문내역", ["주문시각(KST)", "주문시각(ET)", "종류", "구분", "수량", "지정가", "상태", "체결수량", "평균체결가",
                        "키움 주문번호", "원주문", "주문ID", "메모", "비고"],
           [[o["placed_kst"], o["placed_et"], {"NEW": "신규", "MODIFY": "정정", "CANCEL": "취소"}.get(o["kind"], o["kind"]),
             "매수" if o["side"] == "BUY" else "매도", o["qty"], o["price"], o["status"], o["filled_qty"],
             o["avg_fill_price"], o["ord_no"], o["parent_id"], o["order_id"], o["note"] or "",
             "가상" if o["dry_run"] else ""] for o in store.orders_between(d1, d2)],
           {"주문시각(KST)": 20, "주문시각(ET)": 20, "주문ID": 24, "원주문": 24, "메모": 28})

    _sheet(wb, "엔진원장변화", ["거래일", "시각(ET)", "시각(KST)", "내용", "수량", "실현손익(순)", "실현손익(총)/금액", "수수료",
                          "당시 현재가", "층수", "가용현금", "상세"],
           [[e["trade_date"], e["et"][11:], e["kst"], KIND_KO.get(e["kind"], e["kind"]), e["qty"], e["realized_net"],
             e["realized_gross"], e["fee"], e["price"], e["tiers"], e["free_cash"], e["detail"][:500]] for e in s["ledger"]],
           {"시각(KST)": 20, "내용": 20, "상세": 70})

    _sheet(wb, "현재지층", ["층", "수량", "단가", "매수원금", "유형", "합병 회차"],
           [[b["tier"], b["qty"], b["price"], b["cost"], b["type"], b["lots"]] for b in pos["buckets"]])
    _sheet(wb, "자산추이", ["시각(KST)", "거래일", "현재가", "보유수량", "평단", "매수원금", "평가금액", "봇 가용현금",
                        "평가 총자산", "누적 실현", "층수", "계좌 달러예수금"],
           [[x["kst"], x["trade_date"], x["price"], x["qty"], x["avg_price"], x["invested"], x["eval_amount"],
             x["free_cash"], x["equity"], x["realized_cum"], x["tiers"], x["broker_cash"]] for x in s["snapshots"]],
           {"시각(KST)": 20})
    _sheet(wb, "운영이력", ["시각(KST)", "종류", "제목", "내용"],
           [[e["kst"], e["kind"], e["title"], e["body"][:1500]] for e in store.events_between(d1, d2)],
           {"시각(KST)": 20, "종류": 16, "제목": 50, "내용": 100})
    path = config.REPORT_DIR / f"kiw_soxl_{tail}.xlsx"
    wb.save(path)
    return path
