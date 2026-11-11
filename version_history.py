# -*- coding: utf-8 -*-
"""kiw_soxl_trader - 버전 이력 (릴리스마다 최상단에 추가)"""
import hashlib

import config

CURRENT_VERSION = "1.0.0"

HISTORY = [
    ("1.0.0", "2026-11-11",
     "첫 릴리스 — 토스증권용 '인피니트 스노우볼 v3.0'(pipios) SOXL 스나이퍼를 키움증권 REST API(미국주식)로 포팅. "
     "① 원본 소스 15개는 engine/ 에 받은 그대로 두고, 통신만 키움으로 바꿔 끼우는 2개 블록 "
     "(kiwoom_us_client.py = 키움 TR 호출, toss_bridge.py = 토스 주소→키움 TR 번역) ② 실행 폴더 run/real·mock·dry 분리로 "
     "모드별 원장 격리 ③ DRY_RUN 가상 체결 ④ 원본의 깃 자동 업데이트 차단, sync_kiw_soxl(engine_sync.py)로 변경 파일만 반영 + "
     "호환성 점검 ⑤ 매매 기록(SQLite: 주문·체결·엔진 원장 변화·자산 스냅샷·운영이력)과 체결 순간 상황 스냅샷 "
     "⑥ 일간·주간·월간 성과보고(텔레그램 + 이메일, 엑셀 9시트) ⑦ 텔레그램 버튼 메뉴(📋 키움 메뉴: 통합현황·계좌잔고·"
     "보유지층·오늘매매·미체결·운영이력·성과보고·이메일보고·설정·API 부하·버전, 원본 화면 바로가기) "
     "⑧ 안전장치: 주문가능 달러 상한·1회 주문 상한·API 오류 알림·분봉 시각 점검 ⑨ diag.py(키움 응답 실측 점검)"),
]


def engine_fingerprint() -> str:
    """engine/ 원본 소스의 지문(파일 수·해시) — 어떤 원본으로 돌고 있는지 확인용"""
    files = sorted(config.ENGINE_DIR.glob("*.py"))
    if not files:
        return "원본 없음"
    h = hashlib.sha256()
    for p in files:
        h.update(p.name.encode())
        h.update(p.read_bytes())
    title = ""
    readme = config.ENGINE_DIR / "README.md"
    if readme.exists():
        try:
            title = readme.read_text(encoding="utf-8", errors="ignore").splitlines()[0].lstrip("# ").strip()[:60]
        except Exception:
            title = ""
    return f"{len(files)}개 파일 · {h.hexdigest()[:10]}" + (f" · {title}" if title else "")
