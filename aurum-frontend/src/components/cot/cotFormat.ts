import type { Tone } from '@/components/common/Badge';
import type { CotFreshnessStatus, CotSeverity, CotStrength } from '@/types/cot';
import { formatInteger, PLACEHOLDER } from '@/utils/format';

// One hue per screen: the speculative category (the one COT screens ask about) takes the gold;
// hedgers are near-white and everything else a neutral grey. No blue, teal or violet.
export const CATEGORY_COLORS: Record<string, string> = {
  commercial: 'rgb(var(--c-series-2))',
  non_commercial: 'rgb(var(--c-gold))',
  non_reportable: 'rgb(var(--c-series-3))',
  total_reportable: 'rgb(var(--c-line-control))',
  dealer: 'rgb(var(--c-series-2))',
  asset_manager: 'rgb(var(--c-neutral))',
  leveraged_funds: 'rgb(var(--c-gold))',
  other_reportable: 'rgb(var(--c-series-3))',
  producer_merchant: 'rgb(var(--c-series-2))',
  swap_dealer: 'rgb(var(--c-neutral))',
  managed_money: 'rgb(var(--c-gold))',
};

export const signed = (v: number | null | undefined): string => (v == null ? PLACEHOLDER : `${v > 0 ? '+' : ''}${formatInteger(v)}`);

export const fixed = (v: number | null | undefined, digits = 0, sign = false): string =>
  v == null ? PLACEHOLDER : `${sign && v > 0 ? '+' : ''}${v.toFixed(digits)}`;

export const humanize = (s: string | null | undefined): string => (s ? s.replace(/_/g, ' ') : PLACEHOLDER);

export function strengthTone(s: CotStrength | null | undefined): Tone {
  if (!s) return 'neutral';
  if (s.endsWith('bullish')) return 'up';
  if (s.endsWith('bearish')) return 'down';
  return 'neutral';
}

export function freshnessTone(s: CotFreshnessStatus | undefined): Tone {
  return s === 'current' ? 'up' : s === 'update_due' ? 'warn' : s === 'stale' || s === 'no_data' ? 'down' : 'neutral';
}

export function severityTone(s: CotSeverity): Tone {
  return s === 'critical' || s === 'error' ? 'down' : s === 'warning' ? 'warn' : 'info';
}

export const valueTone = (v: number | null | undefined) => (v == null ? 'muted' : v > 0 ? 'up' : v < 0 ? 'down' : 'flat') as 'up' | 'down' | 'flat' | 'muted';
