"""SmartFlow Confluence Bot — CLI.

  python main.py demo                 # offline: synthetic data, full pipeline + backtest
  python main.py update-cot           # pull CFTC COT, compute analytics, store
  python main.py backtest             # real data backtest (yfinance + CFTC)
  python main.py scan                 # analyse latest bars, AI-review any live setup (paper)
  python main.py learn                # component edge report -> suggested weights
"""
from __future__ import annotations
import json
import sys
import yaml
import pandas as pd

from bot import cot as cotm, smc, sessions, agent, learn
from bot.data import load_ohlc, synthetic_ohlc
from bot.storage import Store
from bot.strategy import SmartFlowStrategy, cot_bias_series
from bot import backtest as bt

cfg = yaml.safe_load(open("config.yaml"))
LB = tuple(cfg["cot"]["lookbacks_weeks"])
bias_fn = lambda r: cotm.bias(r, LB)


def prepare(df, cot_an):
    sess_cfg = {k: v for k, v in cfg["sessions"].items() if k != "trade_only_in"}
    d = sessions.tag_sessions(df, sess_cfg, cfg["timezone"])
    rng = sessions.session_ranges(d)
    d = d.join(rng[["asian_high", "asian_low"]], on="trade_day")
    d = smc.enrich(d, cfg["smc"])
    strat = SmartFlowStrategy(cfg)
    strat.prepare(d, cot_bias_series(cot_an, d.index, bias_fn))
    return strat, rng


def store_cot(store, an, market):
    cols = ["commercials_net", "large_specs_net", "small_specs_net", "commercials_idx_52", "commercials_idx_156",
            "commercials_idx_208", "large_specs_idx_52", "large_specs_idx_156", "large_specs_idx_208",
            "small_specs_idx_52", "extreme_bull_count", "extreme_bear_count", "comm_vs_lspec_corr_26w",
            "informed_alignment", "comm_momentum_6w"]
    rows = []
    for ts, r in an.iterrows():
        b, why = bias_fn(r)
        row = {"market": market, "report_date": str(ts.date()), **{c: _f(r.get(c)) for c in cols},
               "commercials_pnl_52w": _f(r.get("commercials_pnl_52w")), "large_specs_pnl_52w": _f(r.get("large_specs_pnl_52w")),
               "small_specs_pnl_52w": _f(r.get("small_specs_pnl_52w")), "bias": b, "reasons": json.dumps(why)}
        rows.append(row)
    store.insert_many("cot_analytics", rows)


def _f(x):
    return None if x is None or pd.isna(x) else float(x)


def cmd_demo():
    store = Store("data/demo.db")
    df = synthetic_ohlc(20000)
    cot_raw = cotm.synthetic_cot(400)
    weekly = df["close"].resample("W-TUE").last()
    an = cotm.analyze(cot_raw, weekly.tz_localize(None) if weekly.index.tz else weekly, LB)
    store_cot(store, an, "SYNTHETIC")
    strat, _ = prepare(df, an)
    trades, eq = bt.run(strat, cfg, store, "SYNTH")
    print("NOTE: synthetic random-walk data has no real edge - this only proves the pipeline works.")
    print(json.dumps(bt.stats(trades, cfg["risk"]["account_size"]), indent=2, default=str))
    print("Rejection funnel:", dict(strat.rejections.most_common()))
    print("\nLatest COT read:", json.dumps(bias_fn(an.iloc[-1]), indent=2))
    print("\nComponent report:", json.dumps([(c["component"], c["trades"], c["avg_r"]) for c in learn.component_report(store, cfg["scoring"], 5)]))


def load_real():
    df = load_ohlc(cfg["symbol"], cfg["timeframes"]["setup"], "730d")
    cot_raw = cotm.fetch_cot(cfg["cot_market"], "2016-01-01")
    daily = load_ohlc(cfg["symbol"], "1d", "15y")["close"]
    weekly = daily.tz_convert(None).resample("W-TUE").last()
    an = cotm.analyze(cot_raw, weekly, LB)
    return df, an, cot_raw.attrs.get("market", cfg["cot_market"])


def cmd_update_cot():
    store = Store(cfg["database"])
    cot_raw = cotm.fetch_cot(cfg["cot_market"], "2016-01-01")
    market = cot_raw.attrs["market"]
    store.insert_many("cot_reports", [{"market": market, "report_date": str(i.date()), **r.to_dict()}
                                      for i, r in cot_raw.iterrows()])
    daily = load_ohlc(cfg["symbol"], "1d", "15y")["close"].tz_convert(None).resample("W-TUE").last()
    an = cotm.analyze(cot_raw, daily, LB)
    store_cot(store, an, market)
    b, why = bias_fn(an.iloc[-1])
    print(f"{market} | report {an.index[-1].date()} | bias {b:+.2f}")
    for w in why: print("  -", w)


def cmd_backtest():
    store = Store(cfg["database"])
    df, an, market = load_real()
    strat, _ = prepare(df, an)
    trades, _ = bt.run(strat, cfg, store, cfg["symbol"])
    print(json.dumps(bt.stats(trades, cfg["risk"]["account_size"]), indent=2, default=str))
    print("Rejection funnel:", dict(strat.rejections.most_common()))
    trades.to_csv("data/backtest_trades.csv", index=False)


def cmd_scan():
    store = Store(cfg["database"])
    df, an, market = load_real()
    strat, rng = prepare(df, an)
    found = None
    for i in range(len(strat.d) - 3, len(strat.d)):
        found = strat.evaluate(i) or found
    b, why = bias_fn(an.iloc[-1])
    print(f"COT bias {b:+.2f}: " + "; ".join(why))
    if not found:
        print("No A-grade setup on the latest bars. Waiting.")
        return
    setup = found.to_dict()
    hist = store.similar_setups(found.direction, found.session)
    news = store.q("SELECT * FROM macro_events WHERE ts >= datetime('now') ORDER BY ts LIMIT 10")
    rev = agent.review(setup, {"bias": b, "reasons": why}, hist, store.memories(), news,
                       cfg["ai_agent"]["model"], cfg["ai_agent"]["effort"]) if cfg["ai_agent"]["enabled"] else {}
    store.insert("signals", dict(symbol=cfg["symbol"], ts=found.time, direction=found.direction, session=found.session,
                                 entry=found.entry, stop=found.stop, target=found.target, rr=found.rr, score=found.score,
                                 breakdown=found.breakdown, reasons=found.reasons, ai_decision=rev.get("decision"),
                                 ai_confidence=rev.get("confidence"), ai_rationale=rev.get("rationale"), mode="paper"))
    if rev.get("lesson_to_store"):
        store.insert("agent_memory", dict(ts=found.time, topic="review", content=rev["lesson_to_store"], source="ai"))
    print(json.dumps({"setup": setup, "ai_review": rev}, indent=2, default=str))


def cmd_learn():
    print(json.dumps(learn.component_report(Store(cfg["database"]), cfg["scoring"]), indent=2))


if __name__ == "__main__":
    {"demo": cmd_demo, "update-cot": cmd_update_cot, "backtest": cmd_backtest,
     "scan": cmd_scan, "learn": cmd_learn}.get(sys.argv[1] if len(sys.argv) > 1 else "demo", cmd_demo)()
