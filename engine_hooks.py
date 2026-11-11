# -*- coding: utf-8 -*-
"""
kiw_soxl_trader - 원본 엔진 적재기 + 연결 고리(hook)

원본(pipios 토스 스나이퍼) 소스는 engine/ 폴더에 "받은 그대로" 둡니다. 이 파일이 하는 일:
  1) engine/*.py 를 실행 폴더(run/<모드>/)로 복사해 그곳에서 불러옵니다.
     → 원장(ledger)·백업이 모드별(real/mock/dry)로 분리되어 서로 섞이지 않고,
       engine/ 폴더는 언제든 새 원본으로 통째로 바꿔도 원장이 지워지지 않습니다.
  2) 엔진의 TossApiClient._request 를 키움 브릿지(toss_bridge.TossBridge.request)로 바꿔 끼웁니다.
     (엔진이 토스 서버로 보내는 모든 통신이 이 한 함수를 거칩니다)
  3) 엔진의 깃 자동 업데이트(git pull → 자가 재기동)를 막습니다. 원본 갱신은 engine_sync.py 로만 합니다.
  4) 텔레그램 명령/버튼 처리 앞에 kiw_soxl 메뉴(telegram_bot.py)를 끼워 넣습니다. 봇 토큰은 하나만 씁니다.
  5) 엔진 컨텍스트(원장·지표)를 붙잡아 기록/보고 모듈이 읽을 수 있게 합니다.

원본이 바뀌어도 이 파일이 의존하는 이름은 아래 HOOK_TARGETS 뿐입니다 (engine_sync.py 가 존재 여부를 점검).
"""
import asyncio
import functools
import importlib
import importlib.util
import os
import shutil
import sys

import config
from logger import get_logger

log = get_logger("hooks")

# (모듈, 클래스 또는 None, 속성) — 원본에서 이 이름이 사라지면 engine_sync 가 경고합니다
HOOK_TARGETS = (
    ("toss_api", "TossApiClient", "_request"),
    ("toss_api", None, "_NonRetryableApiError"),
    ("plugin_updater", None, "execute_git_pull_and_preflight"),
    ("plugin_updater", None, "run_auto_update_loop"),
    ("tg_ui_plugin", "TelegramCommandController", "_handle_command_or_text"),
    ("tg_ui_plugin", "TelegramCommandController", "_handle_callback_query"),
    ("tg_ui_plugin", "TelegramCommandController", "send_message"),
    ("main", "SniperAppContext", "__init__"),
    ("main", None, "async_main"),
)
ENGINE_MODULES = ("toss_api_core", "toss_api", "quant_indicators_plugin", "quant_engine_core", "quant_engine",
                  "sniper_plugin_core", "sniper_plugin", "sniper_exit_plugin", "defense_plugin", "barbell_plugin",
                  "assassin_plugin", "tg_ui_plugin", "plugin_updater", "main_runtime", "main")


class _Runtime:
    context = None        # 엔진 SniperAppContext
    bridge = None         # toss_bridge.TossBridge
    engine_main = None    # 엔진 main 모듈
    run_dir = None
    mode = ""
    modules = {}


runtime = _Runtime()


def run_mode() -> str:
    if config.DRY_RUN:
        return "dry"
    return "mock" if config.KIWOOM_IS_MOCK else "real"


def prepare_run_dir() -> str:
    """engine/*.py → run/<모드>/ 복사 (내용이 다른 파일만). 원장·백업은 run/<모드>/ 에만 존재합니다."""
    mode = run_mode()
    run_dir = config.BASE_DIR / "run" / mode
    run_dir.mkdir(parents=True, exist_ok=True)
    src_files = sorted(p for p in config.ENGINE_DIR.glob("*.py"))
    if not src_files:
        raise RuntimeError(f"engine/ 폴더에 원본 소스가 없습니다: {config.ENGINE_DIR} (sync_kiw_soxl 로 설치하세요)")
    copied = 0
    for p in src_files:
        dst = run_dir / p.name
        if not dst.exists() or dst.read_bytes() != p.read_bytes():
            shutil.copy2(p, dst)
            copied += 1
    names = {p.name for p in src_files}
    for old in run_dir.glob("*.py"):                 # 원본에서 사라진 모듈은 실행 폴더에서도 제거
        if old.name not in names:
            old.unlink()
    for p in config.ENGINE_DIR.glob("LICENSE*"):
        shutil.copy2(p, run_dir / p.name)
    if copied:
        log.info("엔진 소스 %d개를 실행 폴더로 반영: %s", copied, run_dir)
    runtime.run_dir, runtime.mode = str(run_dir), mode
    return str(run_dir)


def _set_engine_env() -> None:
    """엔진이 읽는 환경변수 준비 (토스 키는 쓰이지 않지만 빈 값 경고를 피하려 표식을 넣습니다)"""
    os.environ.setdefault("TOSS_CLIENT_ID", "KIWOOM_BRIDGE")
    os.environ.setdefault("TOSS_CLIENT_SECRET", "KIWOOM_BRIDGE")
    fee = float(config.get("FEE.US_COMMISSION_RATE", 0.0025) or 0.0025)
    os.environ["US_COMMISSION_RATE"] = f"{min(max(fee, 0.000001), 0.01):.6f}"
    os.environ.pop("ROUND_TRIP_FEE_RATE_PCT", None)
    if config.TELEGRAM_ENABLED and config.TELEGRAM_BOT_TOKEN:
        os.environ["TELEGRAM_BOT_TOKEN"] = config.TELEGRAM_BOT_TOKEN
        os.environ["TELEGRAM_CHAT_ID"] = config.TELEGRAM_CHAT_ID
    else:
        for k in ("TELEGRAM_BOT_TOKEN", "TG_BOT_TOKEN", "BOT_TOKEN", "TELEGRAM_TOKEN", "TG_TOKEN"):
            os.environ.pop(k, None)


def load_engine():
    """실행 폴더의 엔진 모듈을 불러옵니다. 우리 main.py 와 이름이 같은 엔진 main.py 는 경로로 직접 적재."""
    run_dir = prepare_run_dir()
    _set_engine_env()
    if run_dir not in sys.path:
        sys.path.append(run_dir)                      # 우리 모듈이 먼저, 엔진 모듈이 나중
    clash = [m for m in ENGINE_MODULES if m != "main" and (config.BASE_DIR / f"{m}.py").exists()]
    if clash:
        raise RuntimeError(f"엔진 모듈과 이름이 겹치는 파일이 봇 폴더에 있습니다: {clash}")
    for name in ENGINE_MODULES:
        if name == "main":
            continue
        runtime.modules[name] = importlib.import_module(name)
    spec = importlib.util.spec_from_file_location("main", os.path.join(run_dir, "main.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["main"] = mod
    spec.loader.exec_module(mod)
    runtime.modules["main"] = mod
    runtime.engine_main = mod
    return mod


def _patch_everywhere(name: str, new_obj) -> int:
    """from X import name 으로 복사된 참조까지 모두 교체"""
    n = 0
    for mod in runtime.modules.values():
        if hasattr(mod, name):
            setattr(mod, name, new_obj)
            n += 1
    return n


def install(bridge) -> None:
    """엔진 적재 후 1회 호출: 브릿지 연결·자동 업데이트 차단·텔레그램/컨텍스트 고리 설치"""
    import store
    import telegram_bot

    m = runtime.modules
    runtime.bridge = bridge
    toss_api = m["toss_api"]
    bridge.error_cls = toss_api._NonRetryableApiError

    # ── 1) 통신 교체: 엔진 → 토스 서버  ⇒  엔진 → 브릿지 → 키움 ─────────
    async def _request(self, method, path, params=None, json_data=None, require_account=False,
                       rate_group="DEFAULT", timeout_sec=10.0, max_retries=2, **kw):
        return await bridge.request(method, path, params=params, json_data=json_data)

    async def _ensure_access_token(self, force_refresh=False):
        return "KIWOOM_BRIDGE"

    toss_api.TossApiClient._request = _request
    toss_api.TossApiClient._ensure_access_token = _ensure_access_token
    toss_api.TossApiClient.kiw_bridge = bridge

    # ── 2) 깃 자동 업데이트 차단 ────────────────────────────────────
    if not config.get("SAFETY.ALLOW_ENGINE_GIT_UPDATE", False):
        msg = "키움 포팅판은 텔레그램/깃 자동 업데이트를 쓰지 않습니다. 새 원본 zip 을 서버 홈에 올린 뒤 sync_kiw_soxl 을 실행하세요."

        async def _no_git_update(*a, **k):
            return False, msg, False

        def _no_git_update_sync(*a, **k):
            return False, msg, False

        async def _idle_update_loop(context=None, *a, **k):
            ev = getattr(context, "shutdown_event", None)
            if ev is not None:
                await ev.wait()

        _patch_everywhere("execute_git_pull_and_preflight", _no_git_update)
        _patch_everywhere("_execute_git_pull_and_preflight_sync", _no_git_update_sync)
        for nm in ("run_auto_update_loop", "auto_update_loop", "start_auto_update_loop",
                   "background_auto_update_loop"):
            _patch_everywhere(nm, _idle_update_loop)

    # ── 3) 엔진 컨텍스트 붙잡기 ─────────────────────────────────────
    ctx_cls = m["main"].SniperAppContext
    orig_init = ctx_cls.__init__

    @functools.wraps(orig_init)
    def _init(self, *a, **k):
        orig_init(self, *a, **k)
        runtime.context = self
        log.info("엔진 컨텍스트 연결 완료 (실행 폴더=%s, 원장=%s)", getattr(self, "base_dir", "?"),
                 os.path.basename(str(getattr(self, "primary_ledger_path", "?"))))

    ctx_cls.__init__ = _init

    # ── 4) 텔레그램: 우리 메뉴를 먼저, 나머지는 원본으로 ────────────
    def _wrap_tg(cls):
        if getattr(cls, "_kiw_wrapped", False):
            return
        o_cmd, o_cb, o_send = cls._handle_command_or_text, cls._handle_callback_query, cls.send_message

        async def _cmd(self, chat_id, raw_text):
            try:
                if await telegram_bot.handle_text(self, str(chat_id), str(raw_text or "")):
                    return
            except Exception as e:
                log.exception("kiw 메뉴 명령 처리 오류: %s", e)
            return await o_cmd(self, chat_id=chat_id, raw_text=raw_text)

        async def _cb(self, cb):
            try:
                if await telegram_bot.handle_callback(self, cb):
                    return
            except Exception as e:
                log.exception("kiw 메뉴 버튼 처리 오류: %s", e)
            return await o_cb(self, cb)

        async def _send(self, text, chat_id=None, reply_markup=None, parse_mode="HTML"):
            ok = await o_send(self, text=text, chat_id=chat_id, reply_markup=reply_markup, parse_mode=parse_mode)
            if chat_id is None:                       # 사용자의 명령에 대한 답이 아닌, 엔진이 스스로 보낸 알림만 기록
                try:
                    plain = telegram_bot.strip_html(str(text))
                    store.add_event("ENGINE_ALERT", plain.split("\n", 1)[0][:200], plain)
                except Exception:
                    pass
            return ok

        cls._handle_command_or_text, cls._handle_callback_query, cls.send_message = _cmd, _cb, _send
        cls._kiw_wrapped = True

    # 하단 고정 키보드에 '📋 키움 메뉴 / 📈 성과보고' 한 줄 추가 (원본 4버튼은 그대로)
    orig_kb = getattr(m["tg_ui_plugin"], "_build_main_reply_keyboard", None)
    if callable(orig_kb):
        def _kb_with_kiw_row(*a, **k):
            kb = orig_kb(*a, **k)
            try:
                rows = list(kb.get("keyboard") or [])
                if not any(b.get("text") == "📋 키움 메뉴" for r in rows for b in r):
                    rows.append(telegram_bot.extra_reply_keyboard_row())
                kb = {**kb, "keyboard": rows}
            except Exception:
                pass
            return kb
        _patch_everywhere("_build_main_reply_keyboard", _kb_with_kiw_row)

    _wrap_tg(m["tg_ui_plugin"].TelegramCommandController)
    bridge_cls = getattr(m["main_runtime"], "_AdaptiveTelegramBridge", None)
    if bridge_cls is not None:
        _wrap_tg(bridge_cls)
    log.info("엔진 연결 고리 설치 완료 (모드=%s, DRY_RUN=%s)", runtime.mode, config.DRY_RUN)


def mirror_ledgers() -> int:
    """실행 폴더의 원장 파일을 data/ledger_mirror/<모드>/ 로 복사 (엔진 자체 3중 백업과 별개의 추가 사본)"""
    if not runtime.run_dir:
        return 0
    dst = config.DATA_DIR / "ledger_mirror" / runtime.mode
    dst.mkdir(parents=True, exist_ok=True)
    n = 0
    for p in list(config.BASE_DIR.joinpath("run", runtime.mode).glob("*ledger*.json")):
        try:
            shutil.copy2(p, dst / p.name)
            n += 1
        except Exception:
            pass
    return n


def restore_ledgers_if_missing() -> list:
    """실행 폴더에 원장이 하나도 없는데 사본이 있으면 되살립니다 (폴더를 실수로 지운 경우 대비)"""
    run_dir = config.BASE_DIR / "run" / run_mode()
    src = config.DATA_DIR / "ledger_mirror" / run_mode()
    if not src.exists() or list(run_dir.glob("*ledger*.json")):
        return []
    restored = []
    run_dir.mkdir(parents=True, exist_ok=True)
    for p in src.glob("*ledger*.json"):
        shutil.copy2(p, run_dir / p.name)
        restored.append(p.name)
    return restored


async def run_engine() -> int:
    """엔진 본체 실행 (엔진의 async_main 을 그대로 호출)"""
    res = runtime.engine_main.async_main()
    return await res if asyncio.iscoroutine(res) else int(res or 0)
