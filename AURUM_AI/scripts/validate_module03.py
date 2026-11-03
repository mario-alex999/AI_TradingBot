"""Real-data validation of Module 03 against the candles Module 01 stored (read-only by default).

    python scripts/validate_module03.py                         # as of the last closed hour, default pairs
    python scripts/validate_module03.py --as-of 2026-11-03T14:00 --earlier-hours 24 --out data/validation
    python scripts/validate_module03.py --pairs EURUSD:H1,XAUUSD:M15 --check-persistence

1. Independent re-check: every swing, BOS/CHoCH, displacement, order block, FVG, BPR, liquidity pool and
   sweep/break, premium/discount + OTE and S/R zone is re-derived from the raw market_candles rows using the
   documented definitions (docs/MODULE_03_TECHNICAL_ANALYSIS.md). Module 03's detection code is NOT used for this.
2. Look-ahead / repaint replay: findings known at (as-of - N hours) must appear unchanged in the as-of result;
   nothing may be confirmed after its own as-of; repeated runs must be identical.
3. --check-persistence (writes ta_* tables only): saves the analyses twice plus the earlier as-of and checks that no
   duplicates appear and that the latest zone states are not rolled back.

Never connects to MT5, never places orders, never modifies Module 01/02 tables. Exit 0 = all checks passed.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from aurum.config import Settings
from aurum.database.connection import engine_from_settings

TF = {"M5": timedelta(minutes=5), "M15": timedelta(minutes=15), "H1": timedelta(hours=1),
      "H4": timedelta(hours=4), "D1": timedelta(days=1)}
EPS = 1e-9
ENGINE = None                 # set in main()
# Thresholds the checker needs, taken from the effective Module 03 configuration in main() (config only - no
# Module 03 detection code is imported, so an engine bug cannot hide itself).
WIN = {"internal": (2, 2), "external": (5, 5)}
PD = {"band": 0.05, "ote_low": 0.62, "ote_high": 0.79, "sweet": 0.705}


def ts(v) -> datetime | None:
    if v in (None, ""):
        return None
    d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def load(symbol: str, tf: str, as_of: datetime):
    sql = text("""select c.ts_utc, c.open, c.high, c.low, c.close from market_candles c
                  join instruments i on i.id = c.instrument_id
                  where i.symbol = :s and c.timeframe = :tf and c.is_complete and c.quality_status <> 'invalid'
                  order by c.ts_utc""")
    with ENGINE.connect() as c:
        rows = [r for r in c.execute(sql, {"s": symbol, "tf": tf}).all() if r[0] + TF[tf] <= as_of]
    return [dict(t=r[0], o=float(r[1]), h=float(r[2]), l=float(r[3]), c=float(r[4])) for r in rows]


class Checker:
    def __init__(self, res: dict):
        self.r = res
        self.tf = res["timeframe"]
        self.dt = TF[self.tf]
        self.as_of = ts(res["as_of"])
        self.k = load(res["symbol"], self.tf, self.as_of)
        self.by_t = {x["t"]: i for i, x in enumerate(self.k)}
        self.fail: list[str] = []
        self.counts: Counter = Counter()

    def bad(self, kind: str, msg: str):
        self.fail.append(f"{kind}: {msg}")

    def idx(self, kind, t, index=None):
        t = ts(t)
        i = self.by_t.get(t)
        if i is None:
            self.bad(kind, f"timestamp {t} is not a stored closed candle")
        elif index is not None and i != index:
            self.bad(kind, f"index {index} != candle position {i} for {t}")
        return i

    def not_future(self, kind, *times):
        for t in times:
            t = ts(t)
            if t is not None and t > self.as_of:
                self.bad(kind, f"time {t} is after as_of {self.as_of}")

    # ------------------------------------------------------------------ checks
    def meta(self):
        r = self.r
        self.counts["candles_db"] = len(self.k)
        if r["candles_used"] != len(self.k):
            self.bad("meta", f"candles_used {r['candles_used']} != closed valid candles in DB {len(self.k)}")
        if ts(r["last_candle"]) != self.k[-1]["t"]:
            self.bad("meta", f"last_candle {r['last_candle']} != DB {self.k[-1]['t']}")
        if ts(r["last_candle"]) + self.dt > self.as_of:
            self.bad("meta", "last candle had not closed at as_of (unfinished candle used)")
        if r["provisional"]:
            self.bad("meta", "result is provisional")
        if abs(r["current_price"] - self.k[-1]["c"]) > EPS:
            self.bad("meta", "current_price is not the last closed candle's close")

    def swings(self):
        last = {}
        for s in self.r["swings"]:
            self.counts["swings"] += 1
            i = self.idx("swing", s["timestamp"], s["index"])
            if i is None:
                continue
            L, R = WIN[s["scope"]]
            hi = s["kind"] == "high"
            v = self.k[i]["h"] if hi else self.k[i]["l"]
            if abs(v - s["price"]) > EPS:
                self.bad("swing", f"{s['timestamp']} price {s['price']} != candle {v}")
            left = self.k[max(0, i - L):i]
            right = self.k[i + 1:i + 1 + R]
            if len(left) < L or len(right) < R:
                self.bad("swing", f"{s['timestamp']} lacks a full {L}/{R} window")
            key = "h" if hi else "l"
            ok_left = all((v > x[key]) if hi else (v < x[key]) for x in left)
            ok_right = all((v >= x[key]) if hi else (v <= x[key]) for x in right)
            if not (ok_left and ok_right):
                self.bad("swing", f"{s['scope']} {s['kind']} {s['timestamp']} fails the {L}/{R} pivot rule")
            if s["confirmed_index"] != i + R:
                self.bad("swing", f"{s['timestamp']} confirmed_index {s['confirmed_index']} != {i + R}")
            elif ts(s["confirmed_at"]) != self.k[i + R]["t"] + self.dt:
                self.bad("swing", f"{s['timestamp']} confirmed_at {s['confirmed_at']} != close of candle i+{R}")
            if ts(s["confirmed_at"]) <= ts(s["timestamp"]):
                self.bad("swing", f"{s['timestamp']} confirmed before it formed")
            self.not_future("swing", s["confirmed_at"])
            # label vs previous swing of the same kind/scope (EQ handled by tolerance -> skip EQ labels)
            k2 = (s["scope"], s["kind"])
            prev = last.get(k2)
            lab = s.get("label")
            if prev is not None and lab in ("HH", "LH", "HL", "LL"):
                higher = s["price"] > prev
                if (lab in ("HH", "HL")) != higher:
                    self.bad("swing", f"{s['timestamp']} labelled {lab} but price {s['price']} vs previous {prev}")
            last[k2] = s["price"]

    def structure(self):
        swing_map = {(s["scope"], ts(s["timestamp"]), s["kind"]): s for s in self.r["swings"]}
        seen = Counter()
        for e in self.r["structure_events"]:
            self.counts["structure_events"] += 1
            self.counts[f"{e['scope']}_{e['event_type']}"] += 1
            seen[(e["scope"], e["event_type"], e["direction"], e["break_index"])] += 1
            i = self.idx("structure", e["break_candle_time"], e["break_index"])
            if i is None:
                continue
            c = self.k[i]["c"]
            bull = e["direction"] == "bullish"
            if not ((c > e["broken_level"]) if bull else (c < e["broken_level"])):
                self.bad("structure", f"{e['event_type']} {e['break_candle_time']}: close {c} not beyond {e['broken_level']}")
            if ts(e["confirmed_at"]) != self.k[i]["t"] + self.dt:
                self.bad("structure", f"{e['break_candle_time']}: confirmed_at is not the breakout candle's close")
            sw = swing_map.get((e["scope"], ts(e["broken_swing_time"]), "high" if bull else "low"))
            if sw is None:
                self.bad("structure", f"{e['break_candle_time']}: broken swing {e['broken_swing_time']} not in swings")
            else:
                if abs(sw["price"] - e["broken_level"]) > EPS:
                    self.bad("structure", f"{e['break_candle_time']}: broken level != swing price")
                # the swing must already be confirmed when the breakout candle closes
                if ts(sw["confirmed_at"]) > ts(e["confirmed_at"]):
                    self.bad("structure", f"{e['break_candle_time']}: breaks a swing not yet confirmed "
                                          f"(swing confirmed {sw['confirmed_at']})")
                if (sw["kind"] == "high") != bull:
                    self.bad("structure", f"{e['break_candle_time']}: direction does not match broken swing kind")
            if not e["confirmed"]:
                self.bad("structure", f"{e['break_candle_time']}: unconfirmed event in closed-candle result")
            if e["event_type"] == "CHOCH" and e["previous_bias"] == e["new_bias"]:
                self.bad("structure", f"{e['break_candle_time']}: CHoCH without bias change")
            if e["event_type"] == "BOS" and not e["initial"] and e["previous_bias"] != e["new_bias"]:
                self.bad("structure", f"{e['break_candle_time']}: non-initial BOS changed bias")
            self.not_future("structure", e["confirmed_at"])
        dups = [k for k, n in seen.items() if n > 1]
        if dups:
            self.bad("structure", f"duplicate events {dups[:3]}")

    def displacements(self):
        for d in self.r["displacements"]:
            self.counts["displacements"] += 1
            i = self.idx("displacement", d["timestamp"], d["index"])
            if i is None or d["kind"] != "single":
                continue
            x = self.k[i]
            rng = x["h"] - x["l"]
            bull = d["direction"] == "bullish"
            if (x["c"] > x["o"]) != bull:
                self.bad("displacement", f"{d['timestamp']}: candle colour != {d['direction']}")
            if rng > 0 and abs(abs(x["c"] - x["o"]) / rng - d["body_to_range"]) > 1e-3:
                self.bad("displacement", f"{d['timestamp']}: body/range mismatch")
            if d["body_to_range"] < 0.6 - 1e-9 or d["close_position"] < 0.7 - 1e-9:
                self.bad("displacement", f"{d['timestamp']}: below documented single-candle thresholds")

    def order_blocks(self):
        ids = Counter(o["id"] for o in self.r["order_blocks"])
        if any(n > 1 for n in ids.values()):
            self.bad("order_block", "duplicate ids")
        for o in self.r["order_blocks"]:
            self.counts[f"ob_{o['direction']}"] += 1
            i = self.idx("order_block", o["source_time"], o["source_index"])
            if i is None:
                continue
            x = self.k[i]
            for f, key in (("source_open", "o"), ("source_high", "h"), ("source_low", "l"), ("source_close", "c")):
                if abs(o[f] - x[key]) > EPS:
                    self.bad("order_block", f"{o['id']}: {f} != candle")
            bull = o["direction"] == "bullish"
            if bull and not x["c"] < x["o"]:
                self.bad("order_block", f"{o['id']}: bullish OB source candle is not bearish")
            if not bull and not x["c"] > x["o"]:
                self.bad("order_block", f"{o['id']}: bearish OB source candle is not bullish")
            if o["zone_mode"] == "wick" and (abs(o["initial_top"] - x["h"]) > EPS or abs(o["initial_bottom"] - x["l"]) > EPS):
                self.bad("order_block", f"{o['id']}: wick zone != candle range")
            if not o["initial_top"] > o["initial_bottom"]:
                self.bad("order_block", f"{o['id']}: invalid zone boundaries")
            if o["current_top"] < o["current_bottom"] - EPS or o["current_top"] > o["initial_top"] + EPS \
                    or o["current_bottom"] < o["initial_bottom"] - EPS:
                self.bad("order_block", f"{o['id']}: current zone outside the initial zone")
            det = ts(o["detected_at"])
            if det < ts(o["break_event"]["confirmed_at"]):
                self.bad("order_block", f"{o['id']}: detected before its structure break was confirmed")
            if det <= ts(o["source_time"]) + self.dt - timedelta(0):
                if det < ts(o["source_time"]) + self.dt:
                    self.bad("order_block", f"{o['id']}: detected before its source candle closed")
            for f in ("first_touch_at", "mitigated_at", "invalidated_at", "expired_at"):
                if o.get(f) and ts(o[f]) < det:
                    self.bad("order_block", f"{o['id']}: {f} {o[f]} before detection {det}")
            self.not_future("order_block", o["detected_at"], o.get("first_touch_at"), o.get("mitigated_at"),
                            o.get("invalidated_at"))

    def fvgs(self):
        ids = Counter(f["id"] for f in self.r["fair_value_gaps"])
        if any(n > 1 for n in ids.values()):
            self.bad("fvg", "duplicate ids")
        for g in self.r["fair_value_gaps"]:
            self.counts[f"fvg_{g['direction']}"] += 1
            ii = [self.idx("fvg", t) for t in g["candle_times"]]
            if None in ii:
                continue
            if ii != [ii[0], ii[0] + 1, ii[0] + 2]:
                self.bad("fvg", f"{g['id']}: candles not consecutive")
                continue
            c1, c3 = self.k[ii[0]], self.k[ii[2]]
            if g["direction"] == "bullish":
                ok = c3["l"] > c1["h"] and abs(g["lower"] - c1["h"]) < EPS and abs(g["upper"] - c3["l"]) < EPS
            else:
                ok = c3["h"] < c1["l"] and abs(g["lower"] - c3["h"]) < EPS and abs(g["upper"] - c1["l"]) < EPS
            if not ok:
                self.bad("fvg", f"{g['id']}: gap does not match candles 1 and 3")
            if not g["upper"] > g["lower"]:
                self.bad("fvg", f"{g['id']}: invalid boundaries")
            det = ts(g["detected_at"])
            if det != c3["t"] + self.dt:
                self.bad("fvg", f"{g['id']}: detected_at != close of candle 3")
            for f in ("partially_filled_at", "filled_at", "invalidated_at", "expired_at"):
                v = ts(g.get(f))
                if v is None:
                    continue
                # state-change times are candle open times; the candle must open at/after detection
                if v < det:
                    self.bad("fvg", f"{g['id']}: {f} {v} uses a candle before detection {det}")
                j = self.by_t.get(v)
                if f != "expired_at" and j is not None:
                    x = self.k[j]
                    touched = (x["l"] <= g["upper"]) if g["direction"] == "bullish" else (x["h"] >= g["lower"])
                    if not touched:
                        self.bad("fvg", f"{g['id']}: {f} candle {v} never reached the gap")
            self.not_future("fvg", g["detected_at"])

    def bprs(self):
        fv = {g["id"]: g for g in self.r["fair_value_gaps"]}
        for b in self.r["balanced_price_ranges"]:
            self.counts["bpr"] += 1
            if not b["upper"] > b["lower"]:
                self.bad("bpr", f"{b['id']}: invalid boundaries")
            for k in ("bullish_fvg_id", "bearish_fvg_id"):
                g = fv.get(b[k])
                if g is None:
                    self.bad("bpr", f"{b['id']}: {k} missing")
                elif b["lower"] < g["lower"] - EPS or b["upper"] > g["upper"] + EPS:
                    self.bad("bpr", f"{b['id']}: overlap outside {k}")
                elif ts(b["detected_at"]) < ts(g["detected_at"]):
                    self.bad("bpr", f"{b['id']}: detected before {k}")

    def liquidity(self):
        pools = {p["id"]: p for p in self.r["liquidity_pools"]}
        if len(pools) != len(self.r["liquidity_pools"]):
            self.bad("liquidity", "duplicate pool ids")
        for p in self.r["liquidity_pools"]:
            self.counts[f"pool_{p['kind']}"] += 1
            created = ts(p["created_at"])
            if p["kind"] in ("previous_day_high", "previous_day_low"):
                day = ts(p["member_times"][0]).date()
                ks = [x for x in self.k if x["t"].date() == day]
                lvl = max(x["h"] for x in ks) if p["kind"].endswith("high") else min(x["l"] for x in ks)
                if abs(lvl - p["level"]) > EPS:
                    self.bad("liquidity", f"{p['id']}: level {p['level']} != that day's extreme {lvl}")
                if created < ks[-1]["t"] + self.dt:
                    self.bad("liquidity", f"{p['id']}: created before the day's last candle closed")
            else:
                for mt in p["member_times"]:
                    i = self.idx("liquidity", mt)
                    if i is not None:
                        v = self.k[i]["h"] if p["side"] == "buy_side" else self.k[i]["l"]
                        if p["kind"] in ("swing_high", "swing_low") and abs(v - p["level"]) > EPS:
                            self.bad("liquidity", f"{p['id']}: level != member candle")
            for f in ("swept_at", "broken_at"):
                if p.get(f) and ts(p[f]) < created:
                    self.bad("liquidity", f"{p['id']}: {f} before pool existed")
            self.not_future("liquidity", p["created_at"], p.get("swept_at"), p.get("broken_at"))
        for e in self.r["liquidity_events"]:
            self.counts[f"liq_{e['event']}"] += 1
            i = self.idx("liquidity_event", e["timestamp"], e["index"])
            p = pools.get(e["pool_id"])
            if i is None:
                continue
            x = self.k[i]
            buy = e["side"] == "buy_side"
            if e["event"] == "sweep":
                ok = (x["h"] > e["level"] and x["c"] <= e["level"]) if buy else (x["l"] < e["level"] and x["c"] >= e["level"])
            else:
                ok = (x["c"] > e["level"]) if buy else (x["c"] < e["level"])
            if not ok:
                self.bad("liquidity_event", f"{e['event']} {e['timestamp']} {e['pool_id']}: candle does not show it")
            if ts(e["confirmed_at"]) != x["t"] + self.dt:
                self.bad("liquidity_event", f"{e['timestamp']}: confirmed_at != candle close")
            if p is not None and x["t"] < ts(p["created_at"]):
                self.bad("liquidity_event", f"{e['timestamp']}: uses a candle before pool {p['id']} existed")
            self.not_future("liquidity_event", e["confirmed_at"])

    def pd_ote(self):
        pd = self.r["premium_discount"]
        ext = self.r["structure"]["external"]
        if not pd or not ext.get("last_swing_high"):
            return
        if abs(pd["range_high"] - ext["last_swing_high"]["price"]) > EPS or abs(pd["range_low"] - ext["last_swing_low"]["price"]) > EPS:
            self.bad("premium_discount", "dealing range is not the latest external swing high/low")
        eq = (pd["range_high"] + pd["range_low"]) / 2
        if abs(eq - pd["equilibrium"]) > 1e-9:
            self.bad("premium_discount", "equilibrium != midpoint")
        pos = (pd["current_price"] - pd["range_low"]) / (pd["range_high"] - pd["range_low"])
        if abs(pos - pd["position"]) > 1e-3:
            self.bad("premium_discount", f"position {pd['position']} != {pos:.4f}")
        zone = "equilibrium" if abs(pos - 0.5) <= PD["band"] else ("premium" if pos > 0.5 else "discount")
        if 0 <= pos <= 1 and zone != pd["zone"]:
            self.bad("premium_discount", f"zone {pd['zone']} != {zone}")
        o = self.r["ote"]
        if o and o.get("lower") is not None:
            h, l = pd["range_high"], pd["range_low"]
            rng = h - l
            if o["direction"] == "bullish":
                lo, up, sw = h - PD["ote_high"] * rng, h - PD["ote_low"] * rng, h - PD["sweet"] * rng
            else:
                lo, up, sw = l + PD["ote_low"] * rng, l + PD["ote_high"] * rng, l + PD["sweet"] * rng
            if max(abs(lo - o["lower"]), abs(up - o["upper"]), abs(sw - o["sweet_spot"])) > 1e-6:
                self.bad("ote", f"OTE {o['lower']}-{o['upper']} != {lo}-{up}")
            inside = o["lower"] <= pd["current_price"] <= o["upper"]
            if inside != o["price_inside"]:
                self.bad("ote", "price_inside flag wrong")
            self.counts["ote_checked"] += 1

    def sr(self):
        for z in self.r["support_resistance"]:
            self.counts["sr_zones"] += 1
            if not z["top"] > z["bottom"]:
                self.bad("sr", f"{z['id']}: invalid boundaries")
            if z["touches"] != z["swing_highs"] + z["swing_lows"] or z["touches"] < 2:
                self.bad("sr", f"{z['id']}: touches inconsistent")
            exp = "flip" if z["swing_highs"] and z["swing_lows"] else ("resistance" if z["swing_highs"] else "support")
            if z["role"] != exp:
                self.bad("sr", f"{z['id']}: role {z['role']} != {exp}")
            self.not_future("sr", z["last_time"])

    def run(self):
        for f in (self.meta, self.swings, self.structure, self.displacements, self.order_blocks, self.fvgs,
                  self.bprs, self.liquidity, self.pd_ote, self.sr):
            f()
        return {"symbol": self.r["symbol"], "timeframe": self.tf, "as_of": str(self.as_of),
                "counts": dict(self.counts), "failures": len(self.fail), "examples": self.fail[:15]}



# identity fields per list (state fields such as mitigation may legitimately evolve after T1)
KEYS = {
    "swings": ("scope", "kind", "timestamp", "price", "confirmed_at"),
    "structure_events": ("scope", "event_type", "direction", "broken_level", "broken_swing_time", "break_candle_time",
                         "confirmed_at", "previous_bias", "new_bias"),
    "order_blocks": ("id", "direction", "source_time", "detected_at", "initial_top", "initial_bottom"),
    "fair_value_gaps": ("id", "direction", "lower", "upper", "detected_at"),
    "liquidity_pools": ("id", "kind", "level", "created_at"),
    "liquidity_events": ("event", "pool_id", "timestamp", "confirmed_at", "level"),
}
TIME_FIELD = {"swings": "confirmed_at", "structure_events": "confirmed_at", "order_blocks": "detected_at",
              "fair_value_gaps": "detected_at", "liquidity_pools": "created_at", "liquidity_events": "confirmed_at"}


def _t(v):
    d = datetime.fromisoformat(str(v))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def ident(lst, name):
    return {tuple(str(x.get(k)) for k in KEYS[name]) for x in lst}




def check_persistence(svc, pairs, t1, t2, engine) -> list[str]:
    problems = []

    def snap():
        with engine.connect() as c:
            ev = c.execute(text("select count(*) from ta_structure_events")).scalar()
            zs = {(r[0], r[1], r[2]): r[3] for r in c.execute(text("select symbol, timeframe, zone_uid, state from ta_zones"))}
        return ev, zs

    def run(at):
        for s, tf in pairs:
            svc.cache.clear()
            svc.analyze(s, tf, at, False, True, True)

    run(t2)
    ev1, z1 = snap()
    run(t2)
    ev2, z2 = snap()
    if (ev1, len(z1)) != (ev2, len(z2)):
        problems.append(f"repeat analysis created rows: events {ev1}->{ev2}, zones {len(z1)}->{len(z2)}")
    run(t1)
    _, z3 = snap()
    rolled = [k for k in z2 if z3.get(k) != z2[k]]
    if rolled:
        problems.append(f"historical recalculation rolled back {len(rolled)} zone states, e.g. {rolled[0]}")
    return problems


def _parse_as_of(v: str | None) -> datetime:
    if not v:
        return datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    d = datetime.fromisoformat(v.replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def main() -> int:
    import argparse
    from pathlib import Path

    from aurum.analysis.config import load_analysis_config
    from aurum.analysis.services.analysis_service import TechnicalAnalysisService

    global ENGINE, WIN, PD
    ap = argparse.ArgumentParser(description="Module 03 real-data validation (read-only by default)")
    ap.add_argument("--as-of", help="UTC ISO time (default: start of the current hour)")
    ap.add_argument("--earlier-hours", type=float, default=24.0, help="replay offset for the look-ahead check")
    ap.add_argument("--pairs", default="EURUSD:H1,GBPUSD:M15,XAUUSD:M15,US30:H1", help="SYMBOL:TF,...")
    ap.add_argument("--out", help="folder for the full JSON results (optional)")
    ap.add_argument("--check-persistence", action="store_true", help="also test ta_* persistence (writes ta_* only)")
    a = ap.parse_args()

    cfg = load_analysis_config()
    WIN = {"internal": (cfg.swings.internal.left, cfg.swings.internal.right),
           "external": (cfg.swings.external.left, cfg.swings.external.right)}
    pdc = cfg.premium_discount
    PD = {"band": pdc.equilibrium_band, "ote_low": pdc.ote_low, "ote_high": pdc.ote_high, "sweet": pdc.ote_sweet_spot}
    if cfg.structure.break_mode != "close" or cfg.order_blocks.zone_mode != "wick":
        print("note: break_mode/zone_mode differ from the defaults this checker verifies; those checks may flag")
    settings = Settings()
    ENGINE = engine_from_settings(settings)
    t2 = _parse_as_of(a.as_of)
    t1 = t2 - timedelta(hours=a.earlier_hours)
    pairs = [tuple(p.split(":")) for p in a.pairs.split(",") if p]
    svc = TechnicalAnalysisService(settings, ENGINE)
    failed = False
    print(f"Module 03 real-data validation  as_of={t2:%Y-%m-%d %H:%M} UTC  replay_from={t1:%Y-%m-%d %H:%M} UTC")
    for sym, tf in pairs:
        res = json.loads(json.dumps(svc.analyze(sym, tf, t2).to_dict(), default=str))
        if a.out:
            Path(a.out).mkdir(parents=True, exist_ok=True)
            Path(a.out, f"{sym}_{tf}_{t2:%Y%m%dT%H%M}.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
        if res.get("status") != "ok":
            print(f"  {sym} {tf}: status={res.get('status')} {res.get('messages')} - nothing to verify")
            failed = True
            continue
        out = Checker(res).run()
        c = out["counts"]
        print(f"  {sym} {tf}: {c.get('candles_db')} closed candles, {c.get('swings', 0)} swings, "
              f"{c.get('structure_events', 0)} BOS/CHoCH, {c.get('ob_bullish', 0) + c.get('ob_bearish', 0)} OBs, "
              f"{c.get('fvg_bullish', 0) + c.get('fvg_bearish', 0)} FVGs, {c.get('bpr', 0)} BPRs, "
              f"{sum(v for k, v in c.items() if k.startswith('pool_'))} pools, {c.get('liq_sweep', 0)} sweeps, "
              f"{c.get('sr_zones', 0)} S/R -> {'OK' if not out['failures'] else str(out['failures']) + ' DISCREPANCIES'}")
        for e in out["examples"]:
            print(f"      {e}")
        failed |= bool(out["failures"])
    for sym, tf in pairs:
        a1 = svc.analyze(sym, tf, t1).to_dict()
        a2 = svc.analyze(sym, tf, t2).to_dict()
        svc.cache.clear()
        a2b = svc.analyze(sym, tf, t2).to_dict()
        probs = []
        for name, tf_field in TIME_FIELD.items():
            for label, res, as_of in (("earlier", a1, t1), ("as-of", a2, t2)):
                if any(_t(x[tf_field]) > as_of for x in res[name]):
                    probs.append(f"{label} {name}: confirmed after as_of")
            k1 = ident(a1[name], name)
            k2 = ident([x for x in a2[name] if _t(x[tf_field]) <= t1], name)
            if k1 != k2:
                probs.append(f"{name}: {len(k2 - k1)} appeared retroactively, {len(k1 - k2)} repainted")

        def strip(d):
            return json.dumps({k: v for k, v in d.items() if k != "generated_at"}, default=str, sort_keys=True)
        if strip(a2) != strip(a2b):
            probs.append("not deterministic")
        print(f"  look-ahead/repaint {sym} {tf}: {'OK' if not probs else '; '.join(probs)}")
        failed |= bool(probs)
    if a.check_persistence:
        probs = check_persistence(svc, pairs, t1, t2, ENGINE)
        print(f"  persistence (ta_* tables): {'OK' if not probs else '; '.join(probs)}")
        failed |= bool(probs)
    print("RESULT:", "FAILED" if failed else "ALL CHECKS PASSED")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
