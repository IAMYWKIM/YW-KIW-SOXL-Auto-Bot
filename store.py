# -*- coding: utf-8 -*-
"""
kiw_soxl_trader - 매매 기록 저장소 (SQLite: data/kiw_soxl_journal.db)

매매 복기를 위해 4가지를 남깁니다.
  orders    : 봇이 낸 모든 주문(키움 주문번호·가격·수량·상태·정정/취소 연결)
  fills     : 체결 건별 기록 + 그 순간의 상황(현재가·호가·RSI·VWAP·지층·현금 등)
  ledger_ev : 원본 엔진 원장(ledger)의 변화 — 매수/매도 횟수, 실현손익 증가분, 싸이클 졸업, 예비시드 수혈 등
  snapshots : 일정 간격의 계좌/지층 스냅샷 (자산 곡선용)
  events    : 알림·오류·설정 변경 등 운영 이력
"""
import json
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import config

ET = ZoneInfo("America/New_York")
KST = ZoneInfo("Asia/Seoul")
DB_PATH = config.DATA_DIR / "kiw_soxl_journal.db"

_lock = threading.RLock()
_conn = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS orders(
  order_id TEXT PRIMARY KEY, ord_no TEXT, kind TEXT, parent_id TEXT, symbol TEXT, side TEXT,
  qty INTEGER, price REAL, client_order_id TEXT, placed_ts REAL, placed_kst TEXT, placed_et TEXT,
  date_kst TEXT, date_et TEXT, status TEXT, filled_qty INTEGER DEFAULT 0, avg_fill_price REAL DEFAULT 0,
  recorded_fill_qty INTEGER DEFAULT 0, dry_run INTEGER DEFAULT 0, updated_ts REAL, closed_ts REAL,
  note TEXT, context TEXT);
CREATE INDEX IF NOT EXISTS ix_orders_ordno ON orders(ord_no, date_kst);
CREATE INDEX IF NOT EXISTS ix_orders_status ON orders(status);
CREATE TABLE IF NOT EXISTS fills(
  id INTEGER PRIMARY KEY AUTOINCREMENT, order_id TEXT, ts REAL, kst TEXT, et TEXT, trade_date TEXT,
  symbol TEXT, side TEXT, qty INTEGER, price REAL, amount REAL, fee REAL, session TEXT,
  limit_price REAL, lot_type TEXT, dry_run INTEGER DEFAULT 0, context TEXT);
CREATE INDEX IF NOT EXISTS ix_fills_date ON fills(trade_date);
CREATE TABLE IF NOT EXISTS ledger_ev(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, kst TEXT, et TEXT, trade_date TEXT, kind TEXT,
  qty INTEGER, realized_net REAL, realized_gross REAL, fee REAL, price REAL, tiers INTEGER,
  free_cash REAL, detail TEXT);
CREATE INDEX IF NOT EXISTS ix_ledger_date ON ledger_ev(trade_date);
CREATE TABLE IF NOT EXISTS snapshots(
  ts REAL PRIMARY KEY, kst TEXT, trade_date TEXT, price REAL, qty INTEGER, avg_price REAL,
  invested REAL, eval_amount REAL, free_cash REAL, equity REAL, realized_cum REAL, tiers INTEGER,
  broker_cash REAL, detail TEXT);
CREATE INDEX IF NOT EXISTS ix_snap_date ON snapshots(trade_date);
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, kst TEXT, trade_date TEXT, kind TEXT, title TEXT, body TEXT);
CREATE INDEX IF NOT EXISTS ix_events_date ON events(trade_date);
CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, v TEXT);
"""


def db() -> sqlite3.Connection:
    global _conn
    with _lock:
        if _conn is None:
            _conn = sqlite3.connect(str(DB_PATH), check_same_thread=False, timeout=10)
            _conn.row_factory = sqlite3.Row
            _conn.executescript(SCHEMA)
            _conn.commit()
        return _conn


def now_parts(ts: float = None):
    ts = ts or time.time()
    dk = datetime.fromtimestamp(ts, KST)
    de = datetime.fromtimestamp(ts, ET)
    return ts, dk.strftime("%Y-%m-%d %H:%M:%S"), de.strftime("%Y-%m-%d %H:%M:%S"), de.strftime("%Y-%m-%d")


def session_of(ts: float = None) -> str:
    de = datetime.fromtimestamp(ts or time.time(), ET)
    hm = de.hour * 100 + de.minute
    if de.weekday() >= 5:
        return "휴장"
    if 400 <= hm < 930:
        return "프리장"
    if 930 <= hm < 1600:
        return "정규장"
    if 1600 <= hm < 2000:
        return "애프터"
    return "장외"


def _exec(sql: str, args=()):
    with _lock:
        cur = db().execute(sql, args)
        db().commit()
        return cur


def query(sql: str, args=()) -> list:
    with _lock:
        return [dict(r) for r in db().execute(sql, args).fetchall()]


# ── orders ─────────────────────────────────────────────────────────
def add_order(order_id, ord_no, kind, symbol, side, qty, price, client_order_id="", parent_id="",
              dry_run=False, context=None, status="PENDING"):
    ts, kst, et, d_et = now_parts()
    _exec("INSERT OR REPLACE INTO orders(order_id,ord_no,kind,parent_id,symbol,side,qty,price,client_order_id,"
          "placed_ts,placed_kst,placed_et,date_kst,date_et,status,dry_run,updated_ts,context) "
          "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
          (order_id, ord_no, kind, parent_id, symbol, side, int(qty), float(price), client_order_id or "",
           ts, kst, et, kst[:10].replace("-", ""), d_et.replace("-", ""), status, 1 if dry_run else 0, ts,
           json.dumps(context or {}, ensure_ascii=False)))


def get_order(order_id: str):
    r = query("SELECT * FROM orders WHERE order_id=?", (order_id,))
    return r[0] if r else None


def update_order(order_id: str, **fields):
    if not fields:
        return
    fields["updated_ts"] = time.time()
    cols = ",".join(f"{k}=?" for k in fields)
    _exec(f"UPDATE orders SET {cols} WHERE order_id=?", (*fields.values(), order_id))


def open_orders() -> list:
    return query("SELECT * FROM orders WHERE status IN ('PENDING','PARTIAL_FILLED','PENDING_CANCEL') "
                 "ORDER BY placed_ts")


def recent_orders(limit: int = 30, symbol: str = "") -> list:
    if symbol:
        return query("SELECT * FROM orders WHERE symbol=? ORDER BY placed_ts DESC LIMIT ?", (symbol, limit))
    return query("SELECT * FROM orders ORDER BY placed_ts DESC LIMIT ?", (limit,))


def known_ord_nos(date_kst: str) -> set:
    return {r["ord_no"] for r in query("SELECT ord_no FROM orders WHERE date_kst=?", (date_kst,))}


# ── fills / ledger / snapshots / events ───────────────────────────
def add_fill(order_id, symbol, side, qty, price, fee, limit_price=0.0, lot_type="", dry_run=False, context=None):
    ts, kst, et, d_et = now_parts()
    _exec("INSERT INTO fills(order_id,ts,kst,et,trade_date,symbol,side,qty,price,amount,fee,session,limit_price,"
          "lot_type,dry_run,context) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
          (order_id, ts, kst, et, d_et, symbol, side, int(qty), float(price), round(qty * price, 4),
           round(fee, 4), session_of(ts), float(limit_price or 0), lot_type or "", 1 if dry_run else 0,
           json.dumps(context or {}, ensure_ascii=False)))


def add_ledger_event(kind, qty=0, realized_net=0.0, realized_gross=0.0, fee=0.0, price=0.0, tiers=0,
                     free_cash=0.0, detail=None):
    ts, kst, et, d_et = now_parts()
    _exec("INSERT INTO ledger_ev(ts,kst,et,trade_date,kind,qty,realized_net,realized_gross,fee,price,tiers,"
          "free_cash,detail) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
          (ts, kst, et, d_et, kind, int(qty), round(realized_net, 4), round(realized_gross, 4), round(fee, 4),
           float(price or 0), int(tiers), round(free_cash, 4), json.dumps(detail or {}, ensure_ascii=False)))


def add_snapshot(price, qty, avg_price, invested, eval_amount, free_cash, equity, realized_cum, tiers,
                 broker_cash=0.0, detail=None):
    ts, kst, et, d_et = now_parts()
    _exec("INSERT OR REPLACE INTO snapshots VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
          (ts, kst, d_et, price, int(qty), avg_price, invested, eval_amount, free_cash, equity, realized_cum,
           int(tiers), broker_cash, json.dumps(detail or {}, ensure_ascii=False)))


def add_event(kind: str, title: str, body: str = ""):
    ts, kst, et, d_et = now_parts()
    _exec("INSERT INTO events(ts,kst,trade_date,kind,title,body) VALUES(?,?,?,?,?,?)",
          (ts, kst, d_et, kind, str(title)[:300], str(body)[:4000]))


def kv_get(k: str, default=None):
    r = query("SELECT v FROM kv WHERE k=?", (k,))
    if not r:
        return default
    try:
        return json.loads(r[0]["v"])
    except Exception:
        return default


def kv_set(k: str, v):
    _exec("INSERT OR REPLACE INTO kv(k,v) VALUES(?,?)", (k, json.dumps(v, ensure_ascii=False)))


# ── 기간 조회 (보고서용) ──────────────────────────────────────────
def fills_between(d1: str, d2: str) -> list:
    return query("SELECT * FROM fills WHERE trade_date>=? AND trade_date<=? ORDER BY ts", (d1, d2))


def ledger_between(d1: str, d2: str) -> list:
    return query("SELECT * FROM ledger_ev WHERE trade_date>=? AND trade_date<=? ORDER BY ts", (d1, d2))


def events_between(d1: str, d2: str, kinds=None) -> list:
    rows = query("SELECT * FROM events WHERE trade_date>=? AND trade_date<=? ORDER BY ts", (d1, d2))
    return [r for r in rows if not kinds or r["kind"] in kinds]


def orders_between(d1: str, d2: str) -> list:
    a, b = d1.replace("-", ""), d2.replace("-", "")
    return query("SELECT * FROM orders WHERE date_et>=? AND date_et<=? ORDER BY placed_ts", (a, b))


def snapshots_between(d1: str, d2: str) -> list:
    return query("SELECT * FROM snapshots WHERE trade_date>=? AND trade_date<=? ORDER BY ts", (d1, d2))


def last_snapshot(before_date: str = None):
    if before_date:
        r = query("SELECT * FROM snapshots WHERE trade_date<? ORDER BY ts DESC LIMIT 1", (before_date,))
    else:
        r = query("SELECT * FROM snapshots ORDER BY ts DESC LIMIT 1")
    return r[0] if r else None


def trade_date_today() -> str:
    """미국 동부 기준 거래일 (17:00 ET 이후는 다음 영업 준비 시간이지만 날짜는 그대로 둡니다)"""
    return datetime.now(ET).strftime("%Y-%m-%d")


def prev_trade_date(d: str = None, days: int = 1) -> str:
    base = datetime.strptime(d or trade_date_today(), "%Y-%m-%d")
    while days > 0:
        base -= timedelta(days=1)
        if base.weekday() < 5:
            days -= 1
    return base.strftime("%Y-%m-%d")
