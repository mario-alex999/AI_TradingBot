# SmartFlow Confluence Model — Playbook

## A. Weekly bias: Commitment of Traders

| Group (CFTC Legacy) | Who | How the bot uses them |
|---|---|---|
| **Commercials** | Producers, banks, hedgers — the "street money" | Primary smart money **at extremes**. Index ≥ 80 = heavily net long vs. their own history |
| **Large speculators** (non-commercials) | Hedge funds, CTAs | Right during trends, wrong at turns. Extreme opposite to commercials = reversal fuel |
| **Small speculators** (non-reportables) | Retail | Historically the weakest P&L → contrarian gauge only |

**COT Index** = `100 × (net − min(net, N)) / (max(net, N) − min(net, N))`, computed for N = 52, 156,
208 weeks (12 months, 3 years, 4 years).

**Rule 1 — The extreme setup (your "look into it" condition).**
Commercials index ≥ 80 **and** large-spec index ≤ 20 → bullish bias. Mirror → bearish. The more
look-backs agree (1/3, 2/3, 3/3), the stronger the bias.

**Rule 2 — Street money (who is actually making money).**
For each group: weekly P&L ≈ `net_position(t−1) × Δprice(t)`; summed over 52 weeks.
- Commercials positive → classic mean-reverting regime; trust commercial extremes.
- Large specs positive, commercials negative → trending regime; lean with the large-spec direction
  and require stronger SMC confirmation for counter-trend trades.
- Small specs are **never** followed.

**Rule 3 — Informed money (commercial ↔ large-spec relationship).**
These two are normally mirror images (26-week correlation of weekly changes ≈ −0.8 to −0.95).
- When large specs start adding **in the same direction as commercials** → informed alignment (+).
- Commercial index moving ≥ 20 points in 6 weeks → active accumulation / distribution (+).
- Correlation weakening toward 0 → regime change; reduce size.

Output: bias ∈ [−1, +1] plus reasons, stored weekly in `cot_analytics`.

## B. Daily structure
Daily swings → BOS (continuation) / CHoCH (reversal). Trade only when daily trend agrees with the COT
bias (or COT is neutral and daily trend gives direction).

## C. Sessions (New York time)
| Session | Hours | Role |
|---|---|---|
| Asian | 19:00–00:00 | Builds the range = liquidity pool (highs/lows to be raided) |
| London killzone | 02:00–05:00 | Judas swing: raids Asian high/low, then the real move |
| New York killzone | 07:00–10:00 | Continuation or reversal of London; raids London high/low |
Entries only inside London or New York killzones. Avoid 30 min either side of red-folder news.

## D. Execution model (the 7 steps)
1. **COT bias** (≤ 25 pts)
2. **HTF structure** aligned (15)
3. **Liquidity sweep** of Asian/swing high-low *against* the bias, closing back inside (15)
4. **Displacement**: CHoCH/BOS on setup TF *in* the bias direction after the sweep (required)
5. **POI**: unmitigated **order block** (15) and/or **FVG** (10) overlapping the **OTE** 62–79 %
   retracement (sweet spot 70.5 %) in **discount** for longs / **premium** for shorts (10)
6. **S/R confluence**: entry on a ≥ 2-touch level (5); **session** killzone (5)
7. **Risk**: SL beyond sweep extreme + 0.2 ATR; TP at opposing external liquidity / S&R; RR ≥ 2

Score ≥ 65 → valid; ≥ 85 → A+ (full risk), else 75 % risk. AI agent may reduce or veto.

## E. Order block rules
- Bullish OB = last down-candle before the impulse that broke structure up (mirror for bearish).
- Valid only while unmitigated (price has not traded through it) and < 300 bars old.
- Best OBs: created by displacement that also left an FVG, sit in discount, and swept liquidity first.

## F. Trade management
- At +1R move SL to breakeven (optional; test both).
- Partial 50 % at the first opposing liquidity, run rest to external range.
- Max 2 open trades, 2 % daily loss cap, stop after 3 consecutive losses for the day.
