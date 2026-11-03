import type { Tone } from '@/components/common/Badge';
import type { EventRiskState, VolatilityRegime } from '@/types/intelligence';
import { formatInZone, PLACEHOLDER } from '@/utils/format';

/** Display helpers for Module 04 panels (formatting only; no data is derived here). */
export const humanize = (s: string | null | undefined): string =>
  s ? s.replace(/_/g, ' ').replace(/\b([a-z])/g, (m) => m.toUpperCase()).replace(/\bKz\b/, 'Kill Zone') : PLACEHOLDER;

export const UTC_HM: Intl.DateTimeFormatOptions = { weekday: 'short', day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' };

export const fmtTime = (iso: string | null | undefined, tz: string) => (iso ? formatInZone(iso, tz, UTC_HM) : PLACEHOLDER);

export function fmtMinutes(m: number | null | undefined): string {
  if (m == null) return PLACEHOLDER;
  const v = Math.round(Math.abs(m));
  if (v < 60) return `${v}m`;
  const h = Math.floor(v / 60);
  if (h < 48) return `${h}h ${v % 60}m`;
  return `${Math.floor(h / 24)}d ${h % 24}h`;
}

export const RISK_TONE: Record<EventRiskState, Tone> = {
  NORMAL: 'neutral',
  EVENT_APPROACHING: 'info',
  HIGH_EVENT_RISK: 'warn',
  RELEASE_WINDOW: 'down',
  POST_EVENT_VOLATILITY: 'warn',
  UNKNOWN: 'neutral',
};

export const REGIME_TONE: Record<VolatilityRegime, Tone> = {
  VERY_LOW: 'info',
  LOW: 'info',
  NORMAL: 'neutral',
  HIGH: 'warn',
  VERY_HIGH: 'down',
  UNKNOWN: 'neutral',
};

export const IMPORTANCE_TONE: Record<string, Tone> = { high: 'down', medium: 'warn', low: 'neutral' };

export const fixed = (v: number | null | undefined, d = 2) => (v == null || Number.isNaN(v) ? PLACEHOLDER : v.toFixed(d));
