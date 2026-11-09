import type { Candle } from '@/types/market';
import type { IndicatorConfig, IndicatorId } from '@/stores/chartStore';
import { atr, bollinger, ema, macd, rsi, sma, vwap, type Series } from '@/utils/indicators';
import { CHART_COLORS } from './chartTheme';

export interface LineSpec {
  id: string;
  label: string;
  color: string;
  values: Series;
  kind: 'line' | 'histogram';
  dashed?: boolean;
}

export interface PaneSpec {
  id: IndicatorId;
  title: string;
  lines: LineSpec[];
  levels?: number[];
}

export const INDICATOR_META: Record<IndicatorId, { label: string; pane: 'price' | 'separate'; hasPeriod: boolean }> = {
  sma: { label: 'Moving average (SMA)', pane: 'price', hasPeriod: true },
  ema: { label: 'Exponential MA (EMA)', pane: 'price', hasPeriod: true },
  bollinger: { label: 'Bollinger Bands', pane: 'price', hasPeriod: true },
  vwap: { label: 'VWAP (daily)', pane: 'price', hasPeriod: false },
  rsi: { label: 'RSI', pane: 'separate', hasPeriod: true },
  macd: { label: 'MACD (12, 26, 9)', pane: 'separate', hasPeriod: false },
  atr: { label: 'ATR', pane: 'separate', hasPeriod: true },
};

/** Builds overlay lines for indicators drawn on the price pane. Only enabled ones are returned. */
export function priceOverlays(candles: Candle[], cfg: Record<IndicatorId, IndicatorConfig>): LineSpec[] {
  const closes = candles.map((c) => c.close);
  const out: LineSpec[] = [];
  if (cfg.sma.enabled) out.push({ id: 'sma', label: `SMA ${cfg.sma.period}`, color: CHART_COLORS.series[1]!, values: sma(closes, cfg.sma.period ?? 50), kind: 'line' });
  if (cfg.ema.enabled) out.push({ id: 'ema', label: `EMA ${cfg.ema.period}`, color: CHART_COLORS.series[0]!, values: ema(closes, cfg.ema.period ?? 20), kind: 'line' });
  if (cfg.bollinger.enabled) {
    const b = bollinger(closes, cfg.bollinger.period ?? 20);
    out.push({ id: 'bb-u', label: 'BB upper', color: CHART_COLORS.series[2]!, values: b.upper, kind: 'line', dashed: true });
    out.push({ id: 'bb-m', label: 'BB basis', color: CHART_COLORS.series[2]!, values: b.middle, kind: 'line' });
    out.push({ id: 'bb-l', label: 'BB lower', color: CHART_COLORS.series[2]!, values: b.lower, kind: 'line', dashed: true });
  }
  if (cfg.vwap.enabled) out.push({ id: 'vwap', label: 'VWAP', color: CHART_COLORS.series[4]!, values: vwap(candles), kind: 'line' });
  return out;
}

export function separatePanes(candles: Candle[], cfg: Record<IndicatorId, IndicatorConfig>): PaneSpec[] {
  const closes = candles.map((c) => c.close);
  const panes: PaneSpec[] = [];
  if (cfg.rsi.enabled)
    panes.push({ id: 'rsi', title: `RSI ${cfg.rsi.period}`, levels: [70, 30], lines: [{ id: 'rsi', label: 'RSI', color: CHART_COLORS.series[2]!, values: rsi(closes, cfg.rsi.period ?? 14), kind: 'line' }] });
  if (cfg.macd.enabled) {
    const m = macd(closes);
    panes.push({
      id: 'macd',
      title: 'MACD 12 26 9',
      lines: [
        { id: 'hist', label: 'Histogram', color: CHART_COLORS.border, values: m.histogram, kind: 'histogram' },
        { id: 'macd', label: 'MACD', color: CHART_COLORS.series[1]!, values: m.macd, kind: 'line' },
        { id: 'signal', label: 'Signal', color: CHART_COLORS.series[3]!, values: m.signal, kind: 'line' },
      ],
    });
  }
  if (cfg.atr.enabled)
    panes.push({ id: 'atr', title: `ATR ${cfg.atr.period}`, lines: [{ id: 'atr', label: 'ATR', color: CHART_COLORS.series[3]!, values: atr(candles, cfg.atr.period ?? 14), kind: 'line' }] });
  return panes;
}
