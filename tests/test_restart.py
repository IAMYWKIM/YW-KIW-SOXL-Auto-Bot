"""엔진 자가 재기동이 항상 포팅판 main.py 로 가는지 시험 (v1.0.11, 원본 Case 81·108 대응)
임시 폴더에 복사해 엔진을 실제로 불러온 뒤 os.execv 를 가로채 인자를 확인합니다."""
import os, sys, shutil, tempfile, pathlib
SRC = pathlib.Path(__file__).resolve().parent.parent
tmp = pathlib.Path(tempfile.mkdtemp(prefix="kiw_soxl_rst_"))
shutil.copytree(SRC, tmp / "bot", ignore=shutil.ignore_patterns("run", "data", "logs", "__pycache__", ".env", "config.json"))
bot = tmp / "bot"
os.environ.update({"TRADE_MODE": "MOCK", "DRY_RUN": "true", "KIWOOM_MOCK_APP_KEY": "x", "KIWOOM_MOCK_SECRET_KEY": "x",
                   "TELEGRAM_ENABLED": "false", "TOSS_SHM_BUS_DIR": str(tmp / "shm")})
os.chdir(bot); sys.path.insert(0, str(bot))
import config, engine_hooks
engine_hooks.load_engine()
class B: error_cls = None
engine_hooks.install(B())
m = engine_hooks.runtime.modules
ok = True
def check(n, c, e=""):
    global ok; ok &= bool(c); print(("  ✅ " if c else "  ❌ ") + n, e)
safe = engine_hooks.runtime.safe_restart
for name in ("plugin_updater", "main", "tg_ui_plugin", "main_runtime"):
    check(f"{name}.perform_self_restart_execv 교체됨", getattr(m[name], "perform_self_restart_execv", None) is safe)
got = {}
def fake_execv(exe, argv): got["argv"] = argv; raise SystemExit(0)
os.execv = fake_execv
os.chdir(engine_hooks.runtime.run_dir); sys.argv = ["main.py"]        # 최악의 경우: 상대 경로 + 실행 폴더
try:
    m["plugin_updater"].perform_self_restart_execv(reason="TEST")
except SystemExit:
    pass
target = got.get("argv", ["", "", ""])[2]
check("재기동 대상 = 포팅판 main.py (절대 경로)", target == str(config.BASE_DIR / "main.py"), target)
check("원본 run/<모드>/main.py 가 아님", "/run/" not in target)
check("깃 자동 업데이트 차단 유지", m["plugin_updater"]._execute_git_pull_and_preflight_sync()[0] is False)
print("결과:", "모두 통과" if ok else "실패 있음")
shutil.rmtree(tmp, ignore_errors=True)
