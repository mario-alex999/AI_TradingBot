/**
 * ─────────────────────────────  DEMO DATA  ─────────────────────────────
 * Synthetic, deterministic values for UI development only. They are NOT
 * market prices. Every consumer receives them wrapped as `source: 'demo'`
 * and the UI labels them "DEMO DATA".
 */
import { DEFAULT_INSTRUMENTS, TIMEFRAME_SECONDS } from '@/config/instruments';
import type { Candle, Instrument, Quote, Timeframe } from '@/types/market';
import { hashString, mulberry32 } from './random';

interface DemoProfile {
  base: number;
  digits: number;
  vol: number; // relative per-bar volatility at H1
  spread: number; // points
  volume: number;
}

const PROFILES: Record<string, DemoProfile> = {
  EURUSD: { base: 1.085, digits: 5, vol: 0.0011, spread: 8, volume: 4200 },
  GBPUSD: { base: 1.27, digits: 5, vol: 0.0013, spread: 12, volume: 3600 },
  XAUUSD: { base: 2650, digits: 2, vol: 0.0022, spread: 30, volume: 8100 },
  US30: { base: 43000, digits: 1, vol: 0.0018, spread: 20, volume: 2900 },
};

const fallbackProfile: DemoProfile = { base: 100, digits: 2, vol: 0.002, spread: 10, volume: 1000 };

export function demoProfile(symbol: string): DemoProfile {
  return PROFILES[symbol] ?? fallbackProfile;
}

export function demoInstruments(): Instrument[] {
  return DEFAULT_INSTRUMENTS.map((i) => ({ ...i }));
}

const round = (v: number, digits: number) => Number(v.toFixed(digits));

export function demoCandles(symbol: string, timeframe: Timeframe, count = 500, now = Date.now()): Candle[] {
  const p = demoProfile(symbol);
  const step = TIMEFRAME_SECONDS[timeframe];
  const rand = mulberry32(hashString(`${symbol}:${timeframe}`));
  const scale = Math.sqrt(step / 3600);
  const end = Math.floor(now / 1000 / step) * step;
  const candles: Candle[] = [];
  let price = p.base * (0.97 + rand() * 0.06);
  let drift = 0;
  for (let i = count - 1; i >= 0; i--) {
    const time = end - i * step;
    if (step < 86_400) {
      const wd = new Date(time * 1000).getUTCDay();
      if (wd === 6) continue; // skip Saturdays in demo series
    }
    drift = drift * 0.92 + (rand() - 0.5) * 0.25;
    const move = (rand() - 0.5 + drift * 0.3) * p.vol * scale * 2;
    const open = price;
    const close = open * (1 + move);
    const wick = p.vol * scale * open;
    const high = Math.max(open, close) + rand() * wick * 0.8;
    const low = Math.min(open, close) - rand() * wick * 0.8;
    const volume = Math.round(p.volume * scale * (0.4 + rand() * 1.4));
    candles.push({
      time,
      open: round(open, p.digits),
      high: round(high, p.digits),
      low: round(low, p.digits),
      close: round(close, p.digits),
      volume,
    });
    price = close;
  }
  return candles;
}

export function demoQuotes(symbols?: string[], now = Date.now()): Quote[] {
  const list = symbols?.length ? symbols : DEFAULT_INSTRUMENTS.map((i) => i.symbol);
  return list.map((symbol) => {
    const p = demoProfile(symbol);
    const day = demoCandles(symbol, 'D1', 3, now);
    const h1 = demoCandles(symbol, 'H1', 24, now);
    const last = h1[h1.length - 1];
    const prevClose = day.length >= 2 ? day[day.length - 2]!.close : (last?.open ?? p.base);
    const bid = last?.close ?? p.base;
    const point = 10 ** -p.digits;
    const ask = round(bid + p.spread * point, p.digits);
    const highs = h1.map((c) => c.high);
    const lows = h1.map((c) => c.low);
    return {
      symbol,
      bid,
      ask,
      spread: p.spread,
      dailyChangePct: ((bid - prevClose) / prevClose) * 100,
      dailyHigh: highs.length ? Math.max(...highs) : bid,
      dailyLow: lows.length ? Math.min(...lows) : bid,
      volume: h1.reduce((s, c) => s + c.volume, 0),
      status: 'unknown',
      digits: p.digits,
      updatedAt: new Date(now).toISOString(),
    } satisfies Quote;
  });
}
