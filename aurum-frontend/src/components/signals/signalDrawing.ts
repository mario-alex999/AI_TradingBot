/**
 * Turns a trade candidate into chart drawings: TradingView-style position boxes (reward above/below the entry,
 * risk on the other side), the entry zone, target and stop levels, the signal marker, and the backend's
 * Module 03 structure (order blocks, fair value gaps, liquidity, BOS/CHoCH).
 *
 * Pure functions only: everything is computed from the signal and backend data; nothing is invented.
 */
import type { Candle, ChartOverlay } from '@/types/market';
import type { SignalOutcome } from '@/types/decision';
import type { TradeSignal } from '@/types/trading';
import type { ChartPalette } from '@/theme/theme';
import { formatNumber } from '@/utils/format';

export interface SignalLike extends Pick<TradeSignal, 'direction' | 'entryZone' | 'stopLoss' | 'takeProfit' | 'riskReward' | 'timestamp'> {
  takeProfit2?: number | null;
  grade?: string;
  outcome?: SignalOutcome | null;
}

/** A filled rectangle (top !== bottom) or a horizontal segment (top === bottom) between two bar times. */
export interface Box {
  id: string;
  from: number; // UNIX seconds, a candle time
  to: number | null; // null: to the right edge of the chart
  top: number;
  bottom: number;
  fill: string;
  stroke: string;
  dashed?: boolean;
  label?: string;
  labelColor?: string;
  labelAt?: 'top' | 'bottom';
}

export interface Level {
  id: string;
  price: number;
  color: string;
  title: string;
  style: 'solid' | 'dashed' | 'dotted';
}

export interface Marker {
  time: number;
  position: 'aboveBar' | 'belowBar' | 'inBar';
  shape: 'arrowUp' | 'arrowDown' | 'circle' | 'square';
  color: string;
  text: string;
}

export interface SignalDrawing {
  boxes: Box[];
  levels: Level[];
  markers: Marker[];
  /** Bar the signal was raised on (snapped to the series). */
  signalTime: number | null;
}

/** "#RRGGBB" + alpha -> "rgba(...)". */
export function alpha(hex: string, a: number): string {
  const h = hex.replace('#', '');
  const n = parseInt(h.length === 3 ? h.replace(/(.)/g, '$1$1') : h, 16);
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
}

/** The last candle time at or before `t` (the bar containing it); the first bar when `t` is earlier. */
export function snapTime(t: number, times: number[]): number | null {
  if (!times.length) return null;
  let lo = 0;
  let hi = times.length - 1;
  if (t <= times[0]!) return times[0]!;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (times[mid]! <= t) lo = mid;
    else hi = mid - 1;
  }
  return times[lo]!;
}

const OVERLAY_LABEL: Partial<Record<ChartOverlay['kind'], string>> = {
  order_block: 'OB',
  fair_value_gap: 'FVG',
  liquidity_zone: 'Liquidity',
  break_of_structure: 'BOS',
  change_of_character: 'CHoCH',
  equal_highs_lows: 'EQ',
  previous_day_hl: 'PD',
  previous_week_hl: 'PW',
  asian_session_hl: 'Asia',
  london_session_hl: 'London',
  support_resistance: 'S/R',
};

export function buildSignalDrawing(
  s: SignalLike,
  candles: Candle[],
  overlays: ChartOverlay[],
  p: ChartPalette,
  digits: number,
  opts: { structure?: boolean } = {},
): SignalDrawing {
  const times = candles.map((c) => c.time);
  const fmt = (v: number) => formatNumber(v, digits);
  const signalTime = snapTime(Math.floor(Date.parse(s.timestamp) / 1000), times);
  const boxes: Box[] = [];
  const levels: Level[] = [];
  const markers: Marker[] = [];
  const long = s.direction === 'long';
  const [z1, z2] = s.entryZone[0] <= s.entryZone[1] ? s.entryZone : [s.entryZone[1], s.entryZone[0]];
  const entry = (z1 + z2) / 2;

  // Module 03 structure first, so the trade's own drawings paint on top.
  if (opts.structure !== false) {
    for (const o of overlays) {
      const tag = OVERLAY_LABEL[o.kind] ?? o.kind;
      const tone = o.direction === 'bullish' ? p.up : o.direction === 'bearish' ? p.down : p.grey1;
      const from = o.from != null ? snapTime(o.from, times) : null;
      const to = o.to != null ? snapTime(o.to, times) : null;
      if (o.priceTo != null && from != null) {
        const zoneTone = o.kind === 'fair_value_gap' ? p.green2 : o.kind === 'liquidity_zone' ? p.grey1 : tone;
        boxes.push({
          id: `ov-${o.id}`,
          from,
          to,
          top: Math.max(o.price, o.priceTo),
          bottom: Math.min(o.price, o.priceTo),
          fill: alpha(zoneTone, 0.1),
          stroke: alpha(zoneTone, 0.45),
          label: tag, // the tag only: full labels collide when zones overlap; the dossier lists the details
          labelColor: zoneTone,
        });
      } else if (from != null) {
        const structural = o.kind === 'break_of_structure' || o.kind === 'change_of_character';
        // BOS / CHoCH get a marker with the tag, so their segment stays unlabelled.
        boxes.push({ id: `ov-${o.id}`, from, to, top: o.price, bottom: o.price, fill: 'transparent', stroke: tone, dashed: true, label: structural ? undefined : tag, labelColor: tone });
        if (structural) {
          markers.push({ time: from, position: o.direction === 'bearish' ? 'belowBar' : 'aboveBar', shape: 'circle', color: tone, text: tag });
        }
      } else {
        levels.push({ id: `ov-${o.id}`, price: o.price, color: alpha(tone, 0.8), title: tag, style: 'dotted' });
      }
    }
  }

  if (signalTime != null) {
    // Position tool: reward between entry and target, risk between entry and stop, from the signal bar onward.
    boxes.push({
      id: 'reward',
      from: signalTime,
      to: null,
      top: Math.max(entry, s.takeProfit),
      bottom: Math.min(entry, s.takeProfit),
      fill: alpha(p.up, 0.16),
      stroke: alpha(p.up, 0.55),
      label: `Target ${fmt(s.takeProfit)} · ${s.riskReward.toFixed(2)}R`,
      labelColor: p.up,
      labelAt: long ? 'top' : 'bottom',
    });
    boxes.push({
      id: 'risk',
      from: signalTime,
      to: null,
      top: Math.max(entry, s.stopLoss),
      bottom: Math.min(entry, s.stopLoss),
      fill: alpha(p.down, 0.16),
      stroke: alpha(p.down, 0.55),
      label: `Stop ${fmt(s.stopLoss)} · −1R`,
      labelColor: p.down,
      labelAt: long ? 'bottom' : 'top',
    });
    boxes.push({
      id: 'entry',
      from: signalTime,
      to: null,
      top: z2,
      bottom: z1,
      fill: alpha(p.accent, 0.28),
      stroke: p.accent,
      label: z1 === z2 ? `Entry ${fmt(z1)}` : `Entry ${fmt(z1)} – ${fmt(z2)}`,
      labelColor: p.accent,
      labelAt: long ? 'top' : 'bottom', // away from the signal marker, which sits below a long / above a short
    });
    markers.push({
      time: signalTime,
      position: long ? 'belowBar' : 'aboveBar',
      shape: long ? 'arrowUp' : 'arrowDown',
      color: long ? p.up : p.down,
      text: `${long ? 'LONG' : 'SHORT'}${s.grade ? ` · ${s.grade}` : ''}`,
    });
  }

  levels.push({ id: 'tp', price: s.takeProfit, color: p.up, title: 'TP1', style: 'solid' });
  if (s.takeProfit2 != null) levels.push({ id: 'tp2', price: s.takeProfit2, color: p.up, title: 'TP2', style: 'dashed' });
  levels.push({ id: 'sl', price: s.stopLoss, color: p.down, title: 'SL', style: 'solid' });
  levels.push({ id: 'entry', price: entry, color: p.accent, title: 'Entry', style: 'dashed' });

  const resolved = s.outcome?.resolvedAt ? snapTime(Math.floor(Date.parse(s.outcome.resolvedAt) / 1000), times) : null;
  if (resolved != null && s.outcome) {
    const r = s.outcome.rMultiple;
    const win = r != null && r > 0;
    markers.push({
      time: resolved,
      position: 'inBar',
      shape: 'square',
      color: win ? p.up : r != null && r < 0 ? p.down : p.grey1,
      text: `${s.outcome.result.replace(/_/g, ' ')}${r != null ? ` ${r > 0 ? '+' : ''}${r.toFixed(2)}R` : ''}`,
    });
  }

  markers.sort((a, b) => a.time - b.time);
  return { boxes, levels, markers, signalTime };
}

/** TradingView symbol and interval for "Open in TradingView". */
const TV_SYMBOL: Record<string, string> = {
  EURUSD: 'FX:EURUSD',
  GBPUSD: 'FX:GBPUSD',
  USDJPY: 'FX:USDJPY',
  XAUUSD: 'OANDA:XAUUSD',
  US30: 'OANDA:US30USD',
};
const TV_INTERVAL: Record<string, string> = { D1: 'D', H4: '240', H1: '60', M15: '15', M5: '5' };

export function tradingViewUrl(instrument: string, timeframe: string): string {
  const symbol = TV_SYMBOL[instrument] ?? instrument;
  return `https://www.tradingview.com/chart/?symbol=${encodeURIComponent(symbol)}&interval=${TV_INTERVAL[timeframe] ?? '60'}`;
}
