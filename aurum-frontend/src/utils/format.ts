import { formatDistanceToNowStrict } from 'date-fns';

/** A missing or not-applicable value: one quiet em dash (a real zero is always printed). */
export const PLACEHOLDER = '—';

type NumberStyle = 'en-US' | 'en-GB' | 'de-DE' | 'fr-FR';

export function formatNumber(
  value: number | null | undefined,
  digits = 2,
  locale: NumberStyle = 'en-US',
): string {
  if (value === null || value === undefined || Number.isNaN(value)) return PLACEHOLDER;
  return value.toLocaleString(locale, { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

export function formatPrice(value: number | null | undefined, digits: number): string {
  return formatNumber(value, digits);
}

export function formatInteger(value: number | null | undefined): string {
  return formatNumber(value, 0);
}

export function formatPercent(value: number | null | undefined, digits = 2, signed = false): string {
  if (value === null || value === undefined || Number.isNaN(value)) return PLACEHOLDER;
  const sign = signed && value > 0 ? '+' : '';
  return `${sign}${value.toFixed(digits)}%`;
}

export function formatCurrency(value: number | null | undefined, currency = 'USD'): string {
  if (value === null || value === undefined || Number.isNaN(value)) return PLACEHOLDER;
  return value.toLocaleString('en-US', { style: 'currency', currency, maximumFractionDigits: 2 });
}

export function formatCompact(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return PLACEHOLDER;
  return Intl.NumberFormat('en-US', { notation: 'compact', maximumFractionDigits: 1 }).format(value);
}

/** Formats an instant in the given IANA zone. */
export function formatInZone(
  iso: string | number | Date | null | undefined,
  timeZone: string,
  opts: Intl.DateTimeFormatOptions = { hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23' },
): string {
  if (iso === null || iso === undefined) return PLACEHOLDER;
  const d = iso instanceof Date ? iso : new Date(iso);
  if (Number.isNaN(d.getTime())) return PLACEHOLDER;
  return new Intl.DateTimeFormat('en-GB', { timeZone, ...opts }).format(d);
}

export function formatDateTime(iso: string | null | undefined, timeZone = 'UTC'): string {
  return formatInZone(iso, timeZone, {
    year: 'numeric',
    month: 'short',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hourCycle: 'h23',
  });
}

export function formatDate(iso: string | null | undefined, timeZone = 'UTC'): string {
  return formatInZone(iso, timeZone, { year: 'numeric', month: 'short', day: '2-digit' });
}

export function relativeTime(iso: string | null | undefined): string {
  if (!iso) return PLACEHOLDER;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return PLACEHOLDER;
  return `${formatDistanceToNowStrict(d)} ago`;
}

export function formatDuration(ms: number): string {
  if (ms < 0) ms = 0;
  const totalMinutes = Math.floor(ms / 60_000);
  const days = Math.floor(totalMinutes / 1440);
  const hours = Math.floor((totalMinutes % 1440) / 60);
  const minutes = totalMinutes % 60;
  if (days > 0) return `${days}d ${hours}h ${minutes}m`;
  if (hours > 0) return `${hours}h ${minutes}m`;
  const seconds = Math.floor((ms % 60_000) / 1000);
  return `${minutes}m ${seconds.toString().padStart(2, '0')}s`;
}

export function directionTone(value: number | null | undefined): 'up' | 'down' | 'flat' {
  if (value === null || value === undefined || value === 0) return 'flat';
  return value > 0 ? 'up' : 'down';
}
