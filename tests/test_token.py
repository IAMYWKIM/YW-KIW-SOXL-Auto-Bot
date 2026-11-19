"""토큰 무효(8005) 자동 재발급 · 프로세스 간 토큰 공유 시험 (v1.0.10)"""
import sys, tempfile, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import config
config.DATA_DIR = pathlib.Path(tempfile.mkdtemp())
import kiwoom_us_client as K

class Resp:
    def __init__(s, d, st=200): s._d, s.status_code, s.headers, s.text = d, st, {}, ""
    def json(s): return s._d

class FakeServer:
    """앱키 하나당 유효 토큰은 1개 — 새로 발급하면 이전 토큰은 무효 (최악의 경우를 가정)"""
    def __init__(s): s.n, s.valid, s.issued = 0, None, 0
    def post(s, url, headers=None, json=None, timeout=None):
        if url.endswith("/oauth2/token"):
            s.n += 1; s.issued += 1; s.valid = f"T{s.n}"
            return Resp({"token": s.valid, "expires_dt": "20991231000000", "return_code": 0})
        if headers["authorization"] != f"Bearer {s.valid}":
            return Resp({"return_code": 3, "return_msg": "인증에 실패했습니다[8005:Token이 유효하지 않습니다]"})
        return Resp({"return_code": 0, "return_msg": "ok", "x": 1})

srv = FakeServer()
ok = True
def check(name, cond, extra=""):
    global ok; ok &= bool(cond); print(("  ✅ " if cond else "  ❌ ") + name, extra)

a = K.KiwoomUsClient("https://x", "APPKEY", "SECRET"); a._session = srv
b = K.KiwoomUsClient("https://x", "APPKEY", "SECRET"); b._session = srv
check("봇(a) 첫 조회 성공", a.request("ust21070", "/p", {}).get("x") == 1)
check("기록 스크립트(b)는 새로 발급하지 않고 공유 토큰 사용", b.request("ust21070", "/p", {}).get("x") == 1 and srv.issued == 1, f"발급 {srv.issued}회")
check("봇(a)도 계속 성공 (토큰 뺏기 없음)", a.request("ust21070", "/p", {}).get("x") == 1 and srv.issued == 1)
# 외부(공유 파일을 모르는 다른 프로그램)가 토큰을 새로 받아 a·b 토큰이 무효가 된 경우
srv.n += 1; srv.valid = f"T{srv.n}"
check("실측 형식 8005(code=3) 받으면 자동 재발급 후 성공", a.request("ust21070", "/p", {}).get("x") == 1, f"발급 {srv.issued}회")
check("b 는 a 가 새로 받은 토큰을 파일에서 가져다 씀 (추가 발급 없음)", b.request("ust21070", "/p", {}).get("x") == 1 and srv.issued == 2, f"발급 {srv.issued}회")
e = K.KiwoomApiError("ust21070", 3, "인증에 실패했습니다[8005:Token이 유효하지 않습니다]")
check("토큰 파일 권한 600", oct(a._token_file().stat().st_mode)[-3:] == "600")
print("결과:", "모두 통과" if ok else "실패 있음")
