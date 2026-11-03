import type { Tone } from '@/components/common/Badge';
import type { CotFreshnessStatus, CotSeverity, CotStrength } from '@/types/cot';
import { formatInteger, PLACEHOLDER } from '@/utils/format';

// One hue per screen: the speculative category (the one COT screens ask about) takes the gold;
// hedgers are near-white and everything else a neutral grey. No blue, teal or violet.
export const CATEGORY_COLORS: Record<string, string> = {
  commercial: '#F2F2F3',
  non_commercial: '#D6AD60',
  non_reportable: '#8A8C92',
  total_reportable: '#6E7076',
  dealer: '#F2F2F3',
  asset_manager: '#B4B5BA',
  leveraged_funds: '#D6AD60',
  other_reportable: '#8A8C92',
  producer_merchant: '#F2F2F3',
  swap_dealer: '#B4B5BA',
  managed_money: '#D6AD60',
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
