"""Event-driven backtester: limit entry at POI, SL-first when both hit in one bar (conservative)."""
from __future__ import annotations
import numpy as np
import pandas as pd
from .risk import RiskManager
from .strategy import SmartFlowStrategy


def run(strategy: SmartFlowStrategy, cfg: dict, store=None, symbol="SYM", mode="backtest",
        max_hold: int = 48, cost_r: float = 0.05):
    d = strategy.d
    rm = RiskManager(cfg["risk"])
    h, l, c = d["high"].values, d["low"].values, d["close"].values
    open_trades, results = [], []
    for i in range(len(d)):
        rm.new_bar(d["trade_day"].iloc[i])
        # manage open trades
        for t in list(open_trades):
            if i <= t["bar"]:
                continue
            risk = abs(t["entry"] - t["stop"])
            fav = (h[i] - t["entry"]) if t["dir"] > 0 else (t["entry"] - l[i])
            adv = (t["entry"] - l[i]) if t["dir"] > 0 else (h[i] - t["entry"])
            t["mfe"] = max(t["mfe"], fav / risk); t["mae"] = max(t["mae"], adv / risk)
            hit_sl = l[i] <= t["stop"] if t["dir"] > 0 else h[i] >= t["stop"]
            hit_tp = h[i] >= t["target"] if t["dir"] > 0 else l[i] <= t["target"]
            exit_px = outcome = None
            if hit_sl:
                exit_px, outcome = t["stop"], "loss"
            elif hit_tp:
                exit_px, outcome = t["target"], "win"
            elif i - t["bar"] >= max_hold:
                exit_px, outcome = c[i], "timeout"
            if exit_px is not None:
                r = (exit_px - t["entry"]) * t["dir"] / risk - cost_r
                pnl = r * risk * t["size"]
                rm.close(pnl)
                open_trades.remove(t)
                rec = dict(signal_id=t["signal_id"], symbol=symbol, direction=t["dir"], open_ts=t["ts"],
                           close_ts=str(d.index[i]), entry=t["entry"], stop=t["stop"], target=t["target"],
                           size=t["size"], exit=float(exit_px), outcome=outcome, r_multiple=round(r, 3),
                           pnl=round(pnl, 2), mae_r=round(t["mae"], 2), mfe_r=round(t["mfe"], 2), mode=mode)
                results.append({**rec, "equity": rm.equity, "breakdown": t["breakdown"], "session": t["session"]})
                if store: store.insert("trades", rec)
        # look for new setups
        if not rm.can_trade():
            continue
        s = strategy.evaluate(i)
        if s is None:
            continue
        sid = None
        if store:
            sid = store.insert("signals", dict(symbol=symbol, ts=s.time, direction=s.direction, session=s.session,
                                                entry=s.entry, stop=s.stop, target=s.target, rr=s.rr, score=s.score,
                                                breakdown=s.breakdown, reasons=s.reasons, mode=mode))
        size = rm.size(s.entry, s.stop, s.score)
        rm.open += 1
        open_trades.append(dict(bar=i, ts=s.time, dir=s.direction, entry=s.entry, stop=s.stop, target=s.target,
                                size=size, mfe=0.0, mae=0.0, signal_id=sid, breakdown=s.breakdown, session=s.session))
    return pd.DataFrame(results), rm.equity


def stats(trades: pd.DataFrame, start_equity: float) -> dict:
    if trades.empty:
        return {"trades": 0}
    r = trades["r_multiple"]
    eq = trades["equity"]
    peak = eq.cummax()
    gross_win, gross_loss = r[r > 0].sum(), -r[r < 0].sum()
    return {
        "trades": int(len(r)),
        "win_rate": round(float((r > 0).mean()), 3),
        "avg_R": round(float(r.mean()), 3),
        "total_R": round(float(r.sum()), 2),
        "profit_factor": round(float(gross_win / gross_loss), 2) if gross_loss else float("inf"),
        "max_drawdown_pct": round(float(((eq - peak) / peak).min() * 100), 2),
        "final_equity": round(float(eq.iloc[-1]), 2),
        "return_pct": round(float((eq.iloc[-1] / start_equity - 1) * 100), 2),
        "by_session": trades.groupby("session")["r_multiple"].agg(["count", "mean"]).round(2).to_dict("index"),
    }
