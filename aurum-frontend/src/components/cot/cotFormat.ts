import type { Tone } from '@/components/common/Badge';
import type { CotFreshnessStatus, CotSeverity, CotStrength } from '@/types/cot';
import { formatInteger, PLACEHOLDER } from '@/utils/format';
import { chartPalette } from '@/theme/theme';

// One hue per screen: the speculative category (the one COT screens ask about) takes the gold;
// hedgers are near-white and everything else a neutral grey. No blue, teal or violet.
// Colours follow the active theme (read when a chart renders).
export const CATEGORY_COLORS: Record<string, string> = Object.defineProperties({} as Record<string, string>, {
  commercial: { enumerable: true, get: () => chartPalette().green2 },
  non_commercial: { enumerable: true, get: () => chartPalette().green1 },
  non_reportable: { enumerable: true, get: () => chartPalette().green3 },
  total_reportable: { enumerable: true, get: () => chartPalette().grey2 },
  dealer: { enumerable: true, get: () => chartPalette().green2 },
  asset_manager: { enumerable: true, get: () => chartPalette().grey1 },
  leveraged_funds: { enumerable: true, get: () => chartPalette().green1 },
  other_reportable: { enumerable: true, get: () => chartPalette().green3 },
  producer_merchant: { enumerable: true, get: () => chartPalette().green2 },
  swap_dealer: { enumerable: true, get: () => chartPalette().grey1 },
  managed_money: { enumerable: true, get: () => chartPalette().green1 },
});

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
