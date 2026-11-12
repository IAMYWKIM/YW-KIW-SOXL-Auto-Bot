# kiw_soxl_trader — 키움증권 해외주식 SOXL 스나이퍼 자동매매 봇

**버전: v1.0.6 (2026-11-13)** · 서비스명 `kiwoombot_soxl` · 로그 프리픽스 `[SOXL]`

토스증권용 「인피니트 스노우볼 v3.0」(pipios · SOXL 7층 그리드 스나이퍼)을 **키움증권 REST API(미국주식)** 로
운용하도록 포팅한 프로그램입니다. kiw_scalp_trader / kiw_close_trader 와 코드·상태·텔레그램 봇을 공유하지 않습니다.

## 설계 한 줄 요약
원본 소스는 `engine/` 에 받은 그대로 두고, 원본이 토스 서버로 보내는 통신 한 곳(`TossApiClient._request`)만
키움 번역기로 바꿔 끼웁니다. 원본이 바뀌면 새 zip 을 올리고 `sync_kiw_soxl` 한 번이면 됩니다.

| 블록 | 파일 | 역할 |
|---|---|---|
| ★ 키움 블록 A | `kiwoom_us_client.py` | 키움 미국주식 TR 호출 (usa*/ust*) — 키움과 통신하는 유일한 파일 |
| ★ 키움 블록 B | `toss_bridge.py` | 토스 주소(`/api/v1/…`) → 키움 TR 번역, 토스 응답 모양으로 변환, DRY_RUN 가상 계좌 |
| 연결 | `engine_hooks.py` | 엔진 적재, 통신 교체, 깃 자동업데이트 차단, 텔레그램 메뉴 연결, 모드별 실행 폴더 |
| 원본 갱신 | `engine_sync.py` | 바뀐 파일만 반영 + 호환성 점검 (`sync_kiw_soxl`) |
| 기록·보고 | `store` `trade_context` `scheduler` `reports` `export` `emailer` `notify` | 매매 기록 DB, 일·주·월 보고 |
| 화면 | `telegram_bot` `settings_menu` | 📋 키움 메뉴(버튼) |
| 점검 | `diag.py` · `tests/` | 키움 응답 실측, 가짜 키움 서버 시험 |

## 빠른 시작
```
bash ~/setup_kiw_soxl_trader.sh          # full zip + 이 스크립트를 홈에 올린 뒤
nano ~/kiw_soxl_trader/.env              # 키·토큰 입력 (TRADE_MODE=MOCK, DRY_RUN=true)
cd ~/kiw_soxl_trader && kiw_soxl_venv/bin/python3 diag.py
deploy_kiw_soxl
```

## 꼭 읽을 것
`docs/USER_MANUAL.md` 3장 **"실거래 전 확인 5가지"** — 프리장 주문 접수, 분봉 시간대, 주문조회 필드, 수수료율,
주문가능 달러 상한. 실제 키움 서버 응답으로 확인하기 전에는 실전(REAL) 운용을 시작하지 마세요.

## 라이선스
`engine/` 의 원본 소스는 원저작자(pipios4006-boop)의 AGPL-3.0 라이선스를 따릅니다 (`engine/LICENSE`).
개인 계좌 운용은 자유롭지만, 재배포하거나 네트워크 서비스로 남에게 제공하면 같은 라이선스로 소스를 공개해야 합니다.
