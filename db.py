"""
SQLite 로깅 모듈 — 거래/잔고/스킵 이벤트를 시계열로 저장해 Grafana에서 시각화
"""
import os
import sqlite3
import time
import threading
from contextlib import contextmanager
from typing import Optional

DB_PATH = os.environ.get('TRADING_DB', 'state/trading.db')

_lock = threading.Lock()
_initialized = False


def _ensure_dir():
    d = os.path.dirname(DB_PATH)
    if d and not os.path.exists(d):
        os.makedirs(d, exist_ok=True)


@contextmanager
def _conn():
    _ensure_dir()
    c = sqlite3.connect(DB_PATH, timeout=10)
    c.execute('PRAGMA journal_mode=WAL')
    c.execute('PRAGMA synchronous=NORMAL')
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init_db() -> None:
    global _initialized
    if _initialized:
        return
    with _lock, _conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            mode TEXT,
            entry_ts INTEGER NOT NULL,
            exit_ts INTEGER,
            entry_price REAL NOT NULL,
            exit_price REAL,
            amount REAL,
            margin REAL,
            leverage INTEGER,
            pnl REAL,
            reason TEXT,
            status TEXT DEFAULT 'open'
        );
        CREATE INDEX IF NOT EXISTS idx_trades_entry_ts ON trades(entry_ts);
        CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status);

        CREATE TABLE IF NOT EXISTS balance_history (
            ts INTEGER PRIMARY KEY,
            balance REAL NOT NULL,
            equity REAL,
            positions INTEGER,
            drawdown_pct REAL,
            peak_balance REAL
        );

        CREATE TABLE IF NOT EXISTS skip_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts INTEGER NOT NULL,
            symbol TEXT NOT NULL,
            reason TEXT,
            rsi REAL,
            adx REAL,
            atr_pct REAL,
            roc5 REAL,
            vol_ratio REAL
        );
        CREATE INDEX IF NOT EXISTS idx_skip_ts ON skip_log(ts);

        CREATE TABLE IF NOT EXISTS withdrawals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts INTEGER NOT NULL,
            amount REAL NOT NULL,
            balance_after REAL,
            note TEXT
        );

        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts INTEGER NOT NULL,
            kind TEXT NOT NULL,
            payload TEXT
        );
        """)
    _initialized = True


def now_ms() -> int:
    return int(time.time() * 1000)


def log_trade_open(symbol: str, side: str, mode: str, entry_price: float,
                   amount: float, margin: float, leverage: int) -> int:
    init_db()
    with _lock, _conn() as c:
        cur = c.execute(
            """INSERT INTO trades(symbol, side, mode, entry_ts, entry_price, amount,
                                  margin, leverage, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open')""",
            (symbol, side, mode, now_ms(), entry_price, amount, margin, leverage)
        )
        return cur.lastrowid or 0


def log_trade_close(symbol: str, exit_price: float, pnl: float, reason: str) -> None:
    init_db()
    with _lock, _conn() as c:
        c.execute(
            """UPDATE trades
               SET exit_ts = ?, exit_price = ?, pnl = ?, reason = ?, status = 'closed'
               WHERE symbol = ? AND status = 'open'""",
            (now_ms(), exit_price, pnl, reason, symbol)
        )


def log_balance(balance: float, equity: float, positions: int,
                drawdown_pct: float, peak_balance: float) -> None:
    init_db()
    with _lock, _conn() as c:
        c.execute(
            """INSERT OR REPLACE INTO balance_history
               (ts, balance, equity, positions, drawdown_pct, peak_balance)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (now_ms(), balance, equity, positions, drawdown_pct, peak_balance)
        )


def log_skip(symbol: str, reason: str, rsi: Optional[float], adx: Optional[float],
             atr_pct: Optional[float], roc5: Optional[float],
             vol_ratio: Optional[float]) -> None:
    init_db()
    with _lock, _conn() as c:
        c.execute(
            """INSERT INTO skip_log(ts, symbol, reason, rsi, adx, atr_pct, roc5, vol_ratio)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (now_ms(), symbol, reason, rsi, adx, atr_pct, roc5, vol_ratio)
        )


def log_withdrawal(amount: float, balance_after: float, note: str = '') -> None:
    init_db()
    with _lock, _conn() as c:
        c.execute(
            "INSERT INTO withdrawals(ts, amount, balance_after, note) VALUES (?, ?, ?, ?)",
            (now_ms(), amount, balance_after, note)
        )


def log_event(kind: str, payload: str = '') -> None:
    init_db()
    with _lock, _conn() as c:
        c.execute(
            "INSERT INTO events(ts, kind, payload) VALUES (?, ?, ?)",
            (now_ms(), kind, payload)
        )


def prune_skip_log(days: int = 30) -> None:
    """skip_log 일정 기간 초과 행 삭제 (DB 비대화 방지)"""
    init_db()
    cutoff = now_ms() - days * 86400 * 1000
    with _lock, _conn() as c:
        c.execute("DELETE FROM skip_log WHERE ts < ?", (cutoff,))
