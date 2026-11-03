"""SQLite knowledge store for the AI agent — every piece of data the bot learns from."""
from __future__ import annotations
import json
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS instruments (
  symbol TEXT PRIMARY KEY, cot_market TEXT, asset_class TEXT, pip_size REAL, contract_size REAL);

CREATE TABLE IF NOT EXISTS candles (
  symbol TEXT, timeframe TEXT, ts TEXT, open REAL, high REAL, low REAL, close REAL, volume REAL,
  PRIMARY KEY (symbol, timeframe, ts));

CREATE TABLE IF NOT EXISTS cot_reports (            -- raw CFTC legacy report
  market TEXT, report_date TEXT, oi REAL,
  comm_long REAL, comm_short REAL, lspec_long REAL, lspec_short REAL, sspec_long REAL, sspec_short REAL,
  PRIMARY KEY (market, report_date));

CREATE TABLE IF NOT EXISTS cot_analytics (          -- derived positioning metrics
  market TEXT, report_date TEXT,
  commercials_net REAL, large_specs_net REAL, small_specs_net REAL,
  commercials_idx_52 REAL, commercials_idx_156 REAL, commercials_idx_208 REAL,
  large_specs_idx_52 REAL, large_specs_idx_156 REAL, large_specs_idx_208 REAL,
  small_specs_idx_52 REAL,
  extreme_bull_count INTEGER, extreme_bear_count INTEGER,
  commercials_pnl_52w REAL, large_specs_pnl_52w REAL, small_specs_pnl_52w REAL,
  comm_vs_lspec_corr_26w REAL, informed_alignment REAL, comm_momentum_6w REAL,
  bias REAL, reasons TEXT,
  PRIMARY KEY (market, report_date));

CREATE TABLE IF NOT EXISTS session_ranges (
  symbol TEXT, trade_day TEXT, asian_high REAL, asian_low REAL, london_high REAL, london_low REAL,
  newyork_high REAL, newyork_low REAL, PRIMARY KEY (symbol, trade_day));

CREATE TABLE IF NOT EXISTS zones (                   -- order blocks / FVGs / S&R
  id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT, timeframe TEXT, kind TEXT,
  top REAL, bottom REAL, created_ts TEXT, mitigated_ts TEXT, touches INTEGER);

CREATE TABLE IF NOT EXISTS signals (                 -- every setup the engine found
  id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT, ts TEXT, direction INTEGER, session TEXT,
  entry REAL, stop REAL, target REAL, rr REAL, score REAL, breakdown TEXT, reasons TEXT,
  ai_decision TEXT, ai_confidence REAL, ai_rationale TEXT, mode TEXT);  -- mode: backtest|paper|live

CREATE TABLE IF NOT EXISTS trades (
  id INTEGER PRIMARY KEY AUTOINCREMENT, signal_id INTEGER, symbol TEXT, direction INTEGER,
  open_ts TEXT, close_ts TEXT, entry REAL, stop REAL, target REAL, size REAL,
  exit REAL, outcome TEXT, r_multiple REAL, pnl REAL, mae_r REAL, mfe_r REAL, mode TEXT,
  FOREIGN KEY (signal_id) REFERENCES signals(id));

CREATE TABLE IF NOT EXISTS journal (                 -- trader + AI post-trade reviews
  id INTEGER PRIMARY KEY AUTOINCREMENT, trade_id INTEGER, ts TEXT, author TEXT,
  emotion TEXT, followed_plan INTEGER, notes TEXT, lesson TEXT);

CREATE TABLE IF NOT EXISTS component_stats (         -- learning loop output
  component TEXT PRIMARY KEY, trades INTEGER, win_rate REAL, avg_r REAL, suggested_weight REAL, updated TEXT);

CREATE TABLE IF NOT EXISTS agent_memory (            -- durable lessons/rules the AI should recall
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, topic TEXT, content TEXT, source TEXT);

CREATE TABLE IF NOT EXISTS macro_events (            -- high-impact news to avoid (NFP, CPI, FOMC...)
  ts TEXT, currency TEXT, event TEXT, impact TEXT, PRIMARY KEY (ts, currency, event));
"""


class Store:
    def __init__(self, path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.con = sqlite3.connect(path)
        self.con.row_factory = sqlite3.Row
        self.con.executescript(SCHEMA)

    def q(self, sql, args=()):
        return [dict(r) for r in self.con.execute(sql, args).fetchall()]

    def insert(self, table: str, row: dict, replace: bool = False) -> int:
        row = {k: (json.dumps(v) if isinstance(v, (dict, list)) else v) for k, v in row.items()}
        cols = ",".join(row)
        ph = ",".join("?" * len(row))
        cur = self.con.execute(f"INSERT {'OR REPLACE ' if replace else ''}INTO {table} ({cols}) VALUES ({ph})",
                               tuple(row.values()))
        self.con.commit()
        return cur.lastrowid

    def insert_many(self, table: str, rows: list[dict], replace: bool = True):
        if not rows:
            return
        cols = list(rows[0])
        self.con.executemany(
            f"INSERT {'OR REPLACE ' if replace else ''}INTO {table} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
            [tuple(r[c] for c in cols) for r in rows])
        self.con.commit()

    def similar_setups(self, direction: int, session: str, limit: int = 200) -> dict:
        rows = self.q("""SELECT t.r_multiple, t.outcome, s.score FROM trades t JOIN signals s ON s.id=t.signal_id
                         WHERE s.direction=? AND s.session=? ORDER BY t.id DESC LIMIT ?""",
                      (direction, session, limit))
        if not rows:
            return {"n": 0}
        wins = sum(r["r_multiple"] > 0 for r in rows)
        return {"n": len(rows), "win_rate": round(wins / len(rows), 3),
                "avg_r": round(sum(r["r_multiple"] for r in rows) / len(rows), 3)}

    def memories(self, limit: int = 20) -> list[str]:
        return [f"[{r['topic']}] {r['content']}" for r in
                self.q("SELECT topic, content FROM agent_memory ORDER BY id DESC LIMIT ?", (limit,))]
