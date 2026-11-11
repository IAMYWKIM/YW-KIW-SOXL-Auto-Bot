# -*- coding: utf-8 -*-
"""
kiw_soxl_trader - 원본 엔진 동기화 + 호환성 점검 도구

원본(pipios 토스 스나이퍼) 소스는 자주 바뀝니다. 새 원본을 받았을 때 이 도구가
  1) 지금 engine/ 과 비교해 "바뀐 파일만" 골라 반영하고 (기존 것은 data/engine_backups/ 에 보관)
  2) 키움 포팅판이 기대하는 것(토스 주소·연결 고리 이름)이 새 원본에도 그대로 있는지 점검합니다.
     문제가 있으면 반영을 되돌리고, 무엇을 고쳐야 하는지(대개 toss_bridge.py 의 ROUTES 한 곳) 알려 줍니다.

사용법 (서버에서는 sync_kiw_soxl 스크립트가 대신 불러 줍니다)
  python3 engine_sync.py check                 # 현재 engine/ 점검만
  python3 engine_sync.py diff  <zip 또는 폴더>  # 무엇이 바뀌는지 미리 보기 (반영 안 함)
  python3 engine_sync.py apply <zip 또는 폴더>  # 바뀐 파일만 반영 + 점검 (문제 시 자동 되돌림)
  python3 engine_sync.py apply <...> --force   # 점검 실패여도 반영 유지 (권장하지 않음)
  python3 engine_sync.py rollback              # 가장 최근 보관본으로 되돌리기
"""
import ast
import difflib
import hashlib
import json
import re
import shutil
import sys
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

import config
import engine_hooks
import toss_bridge

BACKUP_DIR = config.DATA_DIR / "engine_backups"
MANIFEST = config.DATA_DIR / "engine_manifest.json"
KEEP_EXTRA = ("README.md", "LICENSE", "API.txt")
KNOWN_THIRD_PARTY = {"numpy", "pandas", "httpx"}
WATCH_CONSTANTS = ("TARGET_SYMBOL", "REAL_ALLOWED_SYMBOLS", "BARBELL_SAFE_SYMBOL", "PORTION_RATIO_LOCKED",
                   "MAX_GRID_TIERS", "MAX_RESERVE_PORTIONS", "DEFAULT_US_COMMISSION_RATE", "ROUND_TRIP_FEE_RATE_PCT",
                   "DEFAULT_INITIAL_SEED_USD", "NEW_CYCLE_BUY_CUTOFF_HM", "NORMAL_LOOP_INTERVAL_SEC",
                   "FAST_SHIELD_LOOP_INTERVAL_SEC", "DEFAULT_UNLOCK_MODE", "MIN_DIP_BUDGET_RATIO")


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _find_source_root(src: Path, tmp: Path) -> Path:
    """zip 이면 풀고, 그 안에서 toss_api.py 가 있는 폴더를 원본 루트로 삼습니다."""
    if src.is_file() and src.suffix.lower() == ".zip":
        with zipfile.ZipFile(src) as z:
            for info in z.infolist():
                name = info.filename
                if name.startswith("/") or ".." in Path(name).parts:
                    raise SystemExit(f"❌ zip 안에 위험한 경로가 있습니다: {name}")
            z.extractall(tmp)
        src = tmp
    hits = sorted(src.rglob("toss_api.py"), key=lambda p: len(p.parts))
    if not hits:
        raise SystemExit(f"❌ {src} 안에서 원본 소스(toss_api.py)를 찾지 못했습니다.")
    return hits[0].parent


def _engine_files(root: Path) -> dict:
    out = {p.name: p for p in root.glob("*.py")}
    for name in KEEP_EXTRA:
        if (root / name).exists():
            out[name] = root / name
    return out


def compare(new_root: Path) -> dict:
    cur, new = _engine_files(config.ENGINE_DIR), _engine_files(new_root)
    res = {"added": [], "changed": [], "removed": [], "same": []}
    for name, p in sorted(new.items()):
        if name not in cur:
            res["added"].append(name)
        elif _sha(cur[name]) != _sha(p):
            a = cur[name].read_text(encoding="utf-8", errors="ignore").splitlines()
            b = p.read_text(encoding="utf-8", errors="ignore").splitlines()
            plus = minus = 0
            for line in difflib.unified_diff(a, b, lineterm="", n=0):
                if line.startswith("+") and not line.startswith("+++"):
                    plus += 1
                elif line.startswith("-") and not line.startswith("---"):
                    minus += 1
            res["changed"].append((name, plus, minus))
        else:
            res["same"].append(name)
    res["removed"] = sorted(n for n in cur if n not in new and n.endswith(".py"))
    return res


def print_compare(res: dict) -> None:
    print(f"\n📦 원본 비교: 새 파일 {len(res['added'])} · 바뀐 파일 {len(res['changed'])} · "
          f"사라진 파일 {len(res['removed'])} · 그대로 {len(res['same'])}")
    for n in res["added"]:
        print(f"   ➕ {n}")
    for n, plus, minus in res["changed"]:
        mark = "  ← 키움 블록 점검 대상" if n in ("toss_api.py", "toss_api_core.py", "main_runtime.py", "plugin_updater.py",
                                                "tg_ui_plugin.py", "main.py") else ""
        print(f"   ✏️  {n}  (+{plus} / -{minus} 줄){mark}")
    for n in res["removed"]:
        print(f"   ➖ {n}")


# ── 정적 분석 (엔진을 실행하지 않고 소스만 읽습니다) ─────────────────
def _parse(root: Path) -> dict:
    trees = {}
    for p in sorted(root.glob("*.py")):
        try:
            trees[p.stem] = ast.parse(p.read_text(encoding="utf-8"), filename=str(p))
        except SyntaxError as e:
            trees[p.stem] = e
    return trees


def _endpoint_literals(tree: ast.AST) -> set:
    found = set()
    inner = {id(v) for n in ast.walk(tree) if isinstance(n, ast.JoinedStr) for v in n.values}
    for node in ast.walk(tree):
        s = None
        if id(node) in inner:
            continue                       # f-string 조각은 f-string 전체로 한 번만 봅니다
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            s = node.value
        elif isinstance(node, ast.JoinedStr):
            s = "".join(v.value if isinstance(v, ast.Constant) and isinstance(v.value, str) else "X" for v in node.values)
        if s and (s.startswith("/api/v") or s.startswith("/oauth2/")) and "\n" not in s and " " not in s:
            found.add(s.split("?", 1)[0])
    return found


def _names_in(tree: ast.AST) -> dict:
    """모듈 최상위 이름과 클래스별 메서드 이름"""
    top, classes = set(), {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            top.add(node.name)
        elif isinstance(node, ast.ClassDef):
            top.add(node.name)
            classes[node.name] = {n.name for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    top.add(t.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            top.add(node.target.id)
        elif isinstance(node, ast.ImportFrom):
            for a in node.names:
                top.add(a.asname or a.name)
        elif isinstance(node, ast.Import):
            for a in node.names:
                top.add((a.asname or a.name).split(".")[0])
    return {"top": top, "classes": classes}


def _constants(trees: dict) -> dict:
    out, soft = {}, {}
    order = sorted(trees, key=lambda m: (m != "toss_api_core", m != "main", m))
    for mod in order:
        tree = trees[mod]
        if isinstance(tree, Exception):
            continue
        for node in tree.body:
            tgt = val = None
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                tgt, val = node.targets[0].id, node.value
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
                tgt, val = node.target.id, node.value
            if tgt in WATCH_CONSTANTS and tgt not in out:
                try:
                    out[tgt] = repr(ast.literal_eval(val))
                except Exception:
                    soft.setdefault(tgt, ast.unparse(val)[:60])
    for k, v in soft.items():
        out.setdefault(k, v)
    return dict(sorted(out.items()))


def check(root: Path = None, save_manifest: bool = True, quiet: bool = False) -> tuple:
    """반환 (치명 문제 목록, 주의 목록, 요약 dict)"""
    root = root or config.ENGINE_DIR
    trees = _parse(root)
    fatal, warn = [], []
    say = (lambda *a: None) if quiet else print

    for mod, t in trees.items():
        if isinstance(t, Exception):
            fatal.append(f"문법 오류: {mod}.py (line {getattr(t, 'lineno', '?')}) {getattr(t, 'msg', t)}")
    ok_trees = {m: t for m, t in trees.items() if not isinstance(t, Exception)}

    # 1) 필수 모듈
    required = list(engine_hooks.ENGINE_MODULES)
    pu = ok_trees.get("plugin_updater")
    if pu is not None:
        for node in pu.body:
            tgt = node.target if isinstance(node, ast.AnnAssign) else (node.targets[0] if isinstance(node, ast.Assign) else None)
            if isinstance(tgt, ast.Name) and tgt.id == "REQUIRED_CORE_MODULES" and node.value is not None:
                try:
                    listed = [x[:-3] for x in ast.literal_eval(node.value)]
                    extra = [x for x in listed if x not in required]
                    gone = [x for x in required if x not in listed]
                    if extra:
                        warn.append(f"원본 필수 모듈이 늘었습니다: {extra} → engine_hooks.ENGINE_MODULES 에 추가하세요")
                    if gone:
                        warn.append(f"원본 필수 모듈에서 빠진 것: {gone}")
                    required = sorted(set(required) | set(listed))
                except Exception:
                    pass
    for mod in required:
        if mod not in trees:
            fatal.append(f"필수 모듈 없음: {mod}.py")
    clash = [m for m in trees if m != "main" and (config.BASE_DIR / f"{m}.py").exists()]
    if clash:
        fatal.append(f"원본 모듈 이름이 키움 포팅판 파일과 겹칩니다: {clash}")

    # 2) 토스 주소 → 브릿지 대응 여부
    endpoints = set()
    for mod, t in ok_trees.items():
        if mod != "mergy":
            endpoints |= _endpoint_literals(t)
    pats = [re.compile(p) for _, p, _ in toss_bridge.ROUTES]
    unmapped = sorted(e for e in endpoints if not e.startswith("/oauth2/")
                      and not any(p.match(re.sub(r"X", "ID", e)) for p in pats))
    for e in unmapped:
        fatal.append(f"브릿지에 없는 토스 주소: {e}  → toss_bridge.py ROUTES 와 처리 함수 추가 필요")

    # 3) 연결 고리 이름
    names = {m: _names_in(t) for m, t in ok_trees.items()}
    for mod, cls, attr in engine_hooks.HOOK_TARGETS:
        info = names.get(mod)
        if info is None:
            continue
        if cls is None:
            if attr not in info["top"]:
                fatal.append(f"연결 고리 대상 없음: {mod}.{attr}")
        elif cls not in info["classes"]:
            fatal.append(f"연결 고리 대상 클래스 없음: {mod}.{cls}")
        elif attr not in info["classes"][cls]:
            fatal.append(f"연결 고리 대상 메서드 없음: {mod}.{cls}.{attr}")

    # 4) 외부 패키지
    local = set(trees) | {p.stem for p in config.BASE_DIR.glob("*.py")}
    third = set()
    for t in ok_trees.values():
        for node in ast.walk(t):
            mods = []
            if isinstance(node, ast.Import):
                mods = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                mods = [node.module.split(".")[0]]
            for m in mods:
                if m not in local and m not in sys.stdlib_module_names and m != "__future__":
                    third.add(m)
    new_pkgs = sorted(third - KNOWN_THIRD_PARTY)
    if new_pkgs:
        warn.append(f"원본이 새 외부 패키지를 씁니다: {new_pkgs} → requirements.txt 에 추가 후 deploy_kiw_soxl")

    # 5) 이전 점검과 비교 (참고 정보)
    api_methods = sorted(names.get("toss_api", {}).get("classes", {}).get("TossApiClient", set()))
    consts = _constants(ok_trees)
    summary = {"checked_at": datetime.now().isoformat(timespec="seconds"),
               "files": {p.name: _sha(p)[:16] for p in sorted(root.glob("*.py"))},
               "endpoints": sorted(endpoints), "api_methods": api_methods, "constants": consts}
    prev = {}
    if MANIFEST.exists():
        try:
            prev = json.loads(MANIFEST.read_text(encoding="utf-8"))
        except Exception:
            prev = {}
    if prev:
        add_m = sorted(set(api_methods) - set(prev.get("api_methods", [])))
        del_m = sorted(set(prev.get("api_methods", [])) - set(api_methods))
        if add_m:
            warn.append(f"TossApiClient 에 새 메서드: {add_m} (새 토스 주소를 쓰면 위 '브릿지에 없는 토스 주소'로 따로 잡힙니다)")
        if del_m:
            warn.append(f"TossApiClient 에서 사라진 메서드: {del_m}")
        for k, v in consts.items():
            if k in prev.get("constants", {}) and prev["constants"][k] != v:
                warn.append(f"원본 주요 상수 변경: {k} = {prev['constants'][k]} → {v}")
        new_ep = sorted(set(endpoints) - set(prev.get("endpoints", [])))
        if new_ep:
            warn.append(f"원본이 새로 쓰기 시작한 토스 주소: {new_ep}")

    say(f"\n🔎 호환성 점검: 모듈 {len(trees)}개 · 토스 주소 {len(endpoints)}종 · TossApiClient 메서드 {len(api_methods)}개")
    for k, v in consts.items():
        say(f"   · {k} = {v}")
    for w in warn:
        say(f"   ⚠️  {w}")
    for f in fatal:
        say(f"   ❌ {f}")
    say("   ✅ 치명 문제 없음 — 키움 블록 수정 없이 사용할 수 있습니다." if not fatal
        else f"   ❌ 치명 문제 {len(fatal)}건 — 이 원본은 그대로는 쓸 수 없습니다.")
    if save_manifest and not fatal:
        MANIFEST.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    return fatal, warn, summary


def _backup() -> Path:
    dst = BACKUP_DIR / datetime.now().strftime("%Y%m%d_%H%M%S")
    dst.mkdir(parents=True, exist_ok=True)
    for name, p in _engine_files(config.ENGINE_DIR).items():
        shutil.copy2(p, dst / name)
    olds = sorted(d for d in BACKUP_DIR.iterdir() if d.is_dir())
    for d in olds[:-10]:
        shutil.rmtree(d, ignore_errors=True)
    return dst


def _restore(backup: Path) -> None:
    for p in config.ENGINE_DIR.glob("*.py"):
        p.unlink()
    for p in backup.iterdir():
        if p.is_file():
            shutil.copy2(p, config.ENGINE_DIR / p.name)


def apply(src: str, force: bool = False) -> int:
    with tempfile.TemporaryDirectory() as td:
        root = _find_source_root(Path(src).expanduser(), Path(td))
        res = compare(root)
        print_compare(res)
        if not (res["added"] or res["changed"] or res["removed"]):
            print("\n✅ 바뀐 것이 없습니다. engine/ 을 그대로 둡니다.")
            check()
            return 0
        fatal_new, _, _ = check(root, save_manifest=False, quiet=True)
        if fatal_new and not force:
            print("\n❌ 새 원본을 미리 점검한 결과 그대로는 쓸 수 없어 반영하지 않았습니다 (engine/ 변화 없음):")
            for f in fatal_new:
                print(f"   - {f}")
            print("   → 위 항목을 키움 블록(toss_bridge.py / engine_hooks.py)에 반영한 새 kiw_soxl_trader 를 배포한 뒤 다시 실행하세요.")
            return 2
        has_engine = bool(list(config.ENGINE_DIR.glob("*.py")))
        backup = _backup() if has_engine else None
        new = _engine_files(root)
        for name in res["added"] + [n for n, _, _ in res["changed"]]:
            shutil.copy2(new[name], config.ENGINE_DIR / name)
        for name in res["removed"]:
            (config.ENGINE_DIR / name).unlink(missing_ok=True)
        print(f"\n📥 반영 완료: {len(res['added']) + len(res['changed'])}개 복사, {len(res['removed'])}개 삭제"
              + (f" (이전 원본 보관: {backup})" if backup else ""))
        fatal, _, _ = check()
        if fatal and not force:
            if backup:
                _restore(backup)
                print("\n↩️  점검 실패로 이전 원본으로 되돌렸습니다.")
            return 2
        print("\n✅ 원본 갱신 완료. 봇을 재시작하면(deploy_kiw_soxl 또는 sudo systemctl restart kiwoombot_soxl) 새 원본으로 돕니다.")
        print("   원장(ledger)은 run/<모드>/ 에 따로 있어 영향받지 않습니다.")
        return 0


def rollback() -> int:
    olds = sorted(d for d in BACKUP_DIR.iterdir() if d.is_dir()) if BACKUP_DIR.exists() else []
    if not olds:
        print("보관된 이전 원본이 없습니다.")
        return 1
    _restore(olds[-1])
    print(f"↩️  {olds[-1].name} 보관본으로 되돌렸습니다.")
    check()
    return 0


def main(argv: list) -> int:
    cmd = argv[1] if len(argv) > 1 else "check"
    if cmd == "check":
        return 2 if check()[0] else 0
    if cmd == "diff" and len(argv) > 2:
        with tempfile.TemporaryDirectory() as td:
            root = _find_source_root(Path(argv[2]).expanduser(), Path(td))
            print_compare(compare(root))
            return 2 if check(root, save_manifest=False)[0] else 0
    if cmd == "apply" and len(argv) > 2:
        return apply(argv[2], force="--force" in argv)
    if cmd == "rollback":
        return rollback()
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
