# -*- coding: utf-8 -*-
"""
kiw_soxl_trader - 설정을 버튼으로 바꾸는 화면 (kiw_scalp_trader 의 🛠 설정현황과 같은 방식)

🟢 ON / 🔴 OFF 토글과 값 후보 버튼(✅=현재값)으로 config.json 을 즉시 바꿉니다. 재시작이 필요 없습니다.
※ 매매 전략 수치(8% 정량·익절 %·층수 등)는 원본 엔진 소스가 정하므로 여기 없습니다.
   작동 ON/OFF·시드·운용 모드는 ⚙️ 전술 제어반(원본 화면)에서 바꿉니다.
"""
import config
import store

# 분야 → [(키, 이름, 종류, 후보값들 또는 None, 표시 함수)]
SECTIONS = {
    "fee": ("💸 수수료", [
        ("FEE.US_COMMISSION_RATE", "편도 수수료율", "choice", [0.0007, 0.001, 0.0015, 0.002, 0.0025], lambda v: f"{v * 100:.2f}%"),
        ("FEE.SELL_EXTRA_FEE_RATE", "매도 추가비용률(기록용)", "choice", [0.0, 0.00003, 0.0001], lambda v: f"{v * 100:.3f}%"),
    ], "본인 계좌의 실제 미국주식 수수료율로 맞추세요. 엔진의 순익·익절 하한 계산에 쓰입니다. (바꾸면 엔진에도 곧바로 알립니다)"),
    "safe": ("🛡 안전장치", [
        ("SAFETY.MAX_BOT_BUYING_POWER_USD", "봇에 보여줄 주문가능 달러 상한", "choice",
         [0, 5000, 10000, 15000, 20000, 30000, 50000], lambda v: "제한 없음" if not v else f"${v:,.0f}"),
        ("SAFETY.MAX_ORDER_USD", "1회 매수 주문 상한", "choice", [0, 1000, 2000, 3000, 5000, 10000],
         lambda v: "제한 없음" if not v else f"${v:,.0f}"),
    ], "원본 엔진은 시드가 바닥나면 계좌에 남은 달러를 예비시드로 자동 수혈합니다. 같은 계좌에 다른 용도의 달러가 있으면 상한을 정하세요."),
    "report": ("🗓 보고", [
        ("REPORT.DAILY_ENABLED", "일간 보고", "bool", None, None),
        ("REPORT.DAILY_TIME_KST", "일간 보고 시각(KST)", "choice", ["06:10", "06:40", "07:10", "08:00", "09:00"], str),
        ("REPORT.WEEKLY_ENABLED", "주간 보고(토)", "bool", None, None),
        ("REPORT.MONTHLY_ENABLED", "월간 보고(1일)", "bool", None, None),
        ("REPORT.ATTACH_EXCEL_TELEGRAM", "텔레그램에 엑셀 첨부", "bool", None, None),
        ("REPORT.SNAPSHOT_INTERVAL_MIN", "자산 스냅샷 간격(분)", "choice", [5, 10, 30, 60], lambda v: f"{v}분"),
    ], "미국 정규장은 한국시간 06:00(서머타임 05:00)에 끝납니다. 그 뒤 시각으로 맞추세요."),
    "alert": ("🔔 알림", [
        ("ALERT.FILL", "체결 한 줄 알림(봇 기록 기준)", "bool", None, None),
        ("ALERT.API_ERROR", "키움 API 오류 알림", "bool", None, None),
        ("ALERT.API_ERROR_COOLDOWN_MIN", "같은 오류 알림 간격", "choice", [10, 30, 60, 180], lambda v: f"{v}분"),
        ("ALERT.STALE_CANDLE_MIN", "분봉 지연 경고 기준", "choice", [3, 5, 10, 30], lambda v: f"{v}분"),
    ], "매수·매도·졸업 알림은 원본 엔진이 따로 보냅니다. 여기는 키움 포팅판이 추가로 보내는 알림입니다."),
    "kiwoom": ("📡 키움 연결", [
        ("KIWOOM.CHART_TZ", "분봉 시간대", "choice", ["AUTO", "ET", "KST"], str),
        ("KIWOOM.QUERY_MIN_INTERVAL", "조회 최소 간격", "choice", [0.2, 0.22, 0.25, 0.34, 0.5], lambda v: f"{v}초"),
        ("KIWOOM.CANDLE_1M_CACHE_SEC", "1분봉 재조회 간격", "choice", [3.0, 4.0, 5.0, 8.0], lambda v: f"{v:g}초"),
        ("KIWOOM.HOLDINGS_CACHE_SEC", "잔고 재조회 간격", "choice", [0.5, 0.8, 1.5, 3.0], lambda v: f"{v:g}초"),
    ], "키움 미국주식 조회는 초당 5회(혼잡 시간 3회) 제한입니다. 간격을 줄이면 빨라지지만 제한에 걸릴 수 있습니다."),
}


def _is_cur(cur, cand) -> bool:
    try:
        return abs(float(cur) - float(cand)) < 1e-12
    except (TypeError, ValueError):
        return str(cur) == str(cand)


def main_view():
    text = ("🛠 <b>설정 (키움 포팅판)</b>\n" + config.runtime_config_summary() +
            "\n\n분야를 고르세요. 작동 ON/OFF·시드·모드는 ⚙️ 전술 제어반에 있습니다.")
    rows = [[(SECTIONS[k][0], f"kx:set:{k}") for k in ("fee", "safe")],
            [(SECTIONS[k][0], f"kx:set:{k}") for k in ("report", "alert")],
            [(SECTIONS["kiwoom"][0], "kx:set:kiwoom"), ("📧 이메일보고", "kx:email")],
            [("⚙️ 전술 제어반", "kx:eng:/settlement"), ("⬅️ 메뉴", "kx:menu")]]
    return text, rows


def section_view(sec: str):
    title, items, note = SECTIONS[sec]
    lines, rows = [f"{title}", f"<i>{note}</i>", ""], []
    for i, (key, name, kind, cands, fmt) in enumerate(items):
        cur = config.get(key)
        if kind == "bool":
            lines.append(f"• {name}: {'🟢 ON' if cur else '🔴 OFF'}")
            rows.append([(f"{'🟢' if cur else '🔴'} {name}", f"kx:tog:{sec}:{i}")])
        else:
            lines.append(f"• {name}: <b>{fmt(cur)}</b>")
            row = []
            for j, c in enumerate(cands):
                row.append((("✅ " if _is_cur(cur, c) else "") + fmt(c), f"kx:val:{sec}:{i}:{j}"))
                if len(row) == 4:
                    rows.append(row)
                    row = []
            if row:
                rows.append(row)
    rows.append([("⬅️ 설정", "kx:set"), ("⬅️ 메뉴", "kx:menu")])
    return "\n".join(lines), rows


def toggle(sec: str, idx: int) -> str:
    key, name = SECTIONS[sec][1][idx][0], SECTIONS[sec][1][idx][1]
    new = not bool(config.get(key))
    config.set_value(key, new)
    store.add_event("SETTING", f"{name} → {'ON' if new else 'OFF'}", key)
    return key


def choose(sec: str, idx: int, j: int) -> str:
    key, name, _, cands, fmt = SECTIONS[sec][1][idx]
    config.set_value(key, cands[j])
    store.add_event("SETTING", f"{name} → {fmt(cands[j])}", key)
    return key


def set_by_text(key: str, raw: str) -> str:
    if config.get(key) is None:
        return f"❌ 없는 설정 키입니다: {key}"
    val = config.parse_value(key, raw)
    config.set_value(key, val)
    store.add_event("SETTING", f"{key} → {val}", "/set")
    return f"✅ {key} = {val}"


def all_keys_text() -> str:
    out = ["🧾 <b>/set 으로 바꿀 수 있는 키</b> (예: <code>/set SAFETY.MAX_ORDER_USD 3000</code>)"]
    for sec, body in config.load().items():
        if isinstance(body, dict):
            for k, v in body.items():
                if not k.startswith("_"):
                    out.append(f"<code>{sec}.{k}</code> = {v}")
    return "\n".join(out)
