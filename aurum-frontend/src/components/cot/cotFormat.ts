import type { Tone } from '@/components/common/Badge';
import type { CotFreshnessStatus, CotSeverity, CotStrength } from '@/types/cot';
import { formatInteger, PLACEHOLDER } from '@/utils/format';

export const CATEGORY_COLORS: Record<string, string> = {
  commercial: '#5B9BEA',
  non_commercial: '#D6AD60',
  non_reportable: '#B58CF0',
  total_reportable: '#8B98AA',
  dealer: '#5B9BEA',
  asset_manager: '#4FD1C5',
  leveraged_funds: '#D6AD60',
  other_reportable: '#8B98AA',
  producer_merchant: '#5B9BEA',
  swap_dealer: '#4FD1C5',
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
