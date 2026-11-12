# -*- coding: utf-8 -*-
"""kiw_soxl_trader - 버전 이력 (릴리스마다 최상단에 추가)"""
import hashlib

import config

CURRENT_VERSION = "1.0.6"

HISTORY = [
    ("1.0.6", "2026-11-13",
     "성과보고 이메일 간편 설정: scripts/email_setup_kiw_soxl.sh (다른 키움 봇 .env 의 SMTP 계정 복사 또는 직접 입력, "
     "EMAIL_TO 지정, config.json 이메일 자동발송·일간·주간·월간·엑셀 첨부 켜기, 테스트 메일, 재시작 확인). "
     "EMAIL_TO 를 비우면 SMTP_USER(본인)로 받음, Gmail 앱 비밀번호의 띄어쓰기 자동 제거, 예시값(보내는계정@…) 미설정 판정"),
    ("1.0.5", "2026-11-12",
     "유지보수 문서·도구: tests/sim_engine.py 추가(봇을 임시 폴더로 복사해 원본 엔진을 실제로 돌리고 매수→익절→졸업·기동 레이더·"
     "오류 0건을 자동 판정), 연결 고리 점검 대상에 _ensure_access_token 추가(10개), engine/ 에 토스 API 명세(API.txt) 동봉, "
     "상세 사용자 매뉴얼 v1.1 (docs/USER_MANUAL.md, PDF)"),
    ("1.0.4", "2026-11-12",
     "기동 직후 🔫 스나이퍼 레이더 화면 자동 발송: 현재가·1분봉 지표가 채워진 뒤(최대 3분 대기) 원본 레이더 화면(/avwap)을 "
     "생존 배너 다음에 1회 보냄. 🛠 설정 → 🔔 알림 → '기동 시 스나이퍼 레이더 화면'(ALERT.STARTUP_RADAR)으로 끄고 켬"),
    ("1.0.3", "2026-11-12",
     "키움 서버 일시 오류 대응 (실측: usa06012 return_code=7 '서비스를 처리하는 중에 오류가 발생했습니다[1631]', 07:53 KST): "
     "① 조회 TR 은 일시 오류·연결 끊김 시 최대 2회 자동 재시도 (주문 TR 은 중복 접수 방지를 위해 재시도 안 함) "
     "② 그래도 실패하면 일봉(3일)·환율(1일)·체결내역·호가는 직전 값으로 이어 감 "
     "③ 일시 오류는 10분 안에 3회 이상 반복될 때만 텔레그램 알림 (ALERT.TRANSIENT_REPEAT), 운영이력에는 모두 기록"),
    ("1.0.2", "2026-11-11",
     "실계좌 diag 실측 반영: ① 키움은 자정을 넘긴 주간거래 체결을 '영업일자+24시 이상'(예: 20261110263300 = 11/11 02:33 ET)으로 "
     "표기 → 분봉·체결내역에서 이 봉들이 통째로 버려지던 문제 수정(실제 시각으로 변환) ② 차트 시작일자는 '그 날짜부터 과거로' "
     "의미 → 일봉이 40일 전 데이터로 오던 문제 수정(오늘 기준), 분봉은 오늘 기준 최신→과거 100봉×4페이지 ③ diag.py 분봉 항목 오류 수정"),
    ("1.0.1", "2026-11-11",
     "첫 실행 오류 수정(usa10100 NO_STEX · ust31301 NO_FX): ① 키움 명세 예시대로 SOXL·SGOV=NY, QQQ=ND 를 기본값으로 "
     "(AUTO 여도 이 값 사용) ② 거래소구분 자동 탐색을 usa10098(종목코드만) → usa10100(거래소별) 순서로 바꾸고, "
     "인증·권한 오류(8xxx)는 'NO_STEX/NO_FX' 로 덮지 않고 실제 키움 오류코드와 원인 안내(IP 미등록·실전/모의 키 혼동·"
     "모의투자 미지원 등)를 텔레그램에 표시 ③ 환율 조회 실패 시 마지막 성공 환율로 대체 ④ diag.py 에 서버 공인 IP 표시"),
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
