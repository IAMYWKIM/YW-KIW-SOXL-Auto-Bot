# -*- coding: utf-8 -*-
"""
kiw_soxl_trader - 실행 진입점 (서비스: kiwoombot_soxl)

순서
  1) 설정 준비(config.json 새 키 보충) → 원장 사본 복구 점검
  2) 원본 엔진(engine/)을 실행 폴더(run/<모드>/)로 반영하고 불러오기
  3) 키움 브릿지를 엔진에 연결(engine_hooks.install)
  4) 기록/보고 백그라운드 작업 시작 → 엔진 본체 실행

원본 엔진의 매매 로직은 손대지 않습니다. 이 파일과 주변 모듈은 "연결·기록·보고" 만 맡습니다.
"""
import asyncio
import os
import sys

sys.argv[0] = os.path.abspath(sys.argv[0])          # 엔진의 자가 재기동(os.execv)이 이 파일로 돌아오게 고정
os.chdir(os.path.dirname(sys.argv[0]))

import config  # noqa: E402
from logger import get_logger  # noqa: E402

log = get_logger("main")


def preflight() -> list:
    problems = []
    if not config.KIWOOM_APP_KEY or "여기에_" in config.KIWOOM_APP_KEY:
        problems.append("키움 앱키가 비어 있습니다 (.env 의 KIWOOM_APP_KEY" + ("_MOCK" if config.KIWOOM_IS_MOCK else "") + ")")
    if not config.KIWOOM_SECRET_KEY or "여기에_" in config.KIWOOM_SECRET_KEY:
        problems.append("키움 시크릿키가 비어 있습니다")
    if not list(config.ENGINE_DIR.glob("*.py")):
        problems.append("engine/ 폴더에 원본 소스가 없습니다 (sync_kiw_soxl 실행)")
    return problems


async def amain() -> int:
    import engine_hooks
    import scheduler
    import store
    import trade_context
    from toss_bridge import TossBridge

    restored = engine_hooks.restore_ledgers_if_missing()
    if restored:
        log.warning("실행 폴더에 원장이 없어 사본에서 복구했습니다: %s", restored)
    engine_hooks.load_engine()
    bridge = TossBridge()
    bridge.context_provider = trade_context.snapshot
    engine_hooks.install(bridge)
    store.add_event("SYSTEM", f"봇 기동 v{__import__('version_history').CURRENT_VERSION}",
                    f"모드={engine_hooks.runtime.mode} DRY_RUN={config.DRY_RUN} base={config.KIWOOM_API_BASE}"
                    + (f" 원장복구={restored}" if restored else ""))
    tasks = scheduler.start_all()
    try:
        return await engine_hooks.run_engine()
    finally:
        for t in tasks:
            t.cancel()
        try:
            engine_hooks.mirror_ledgers()
        except Exception:
            pass


def main() -> int:
    added = config.ensure_config_json()
    if added:
        log.info("config.json 새 키 보충: %s", ", ".join(added[:12]))
    log.info("=" * 60)
    log.info("%s 기동 · 모드=%s · DRY_RUN=%s · %s", config.BOT_LABEL, config.TRADE_MODE, config.DRY_RUN,
             config.KIWOOM_API_BASE)
    problems = preflight()
    if problems:
        for p in problems:
            log.error("기동 중단: %s", p)
        return 2
    try:
        return asyncio.run(amain())
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
