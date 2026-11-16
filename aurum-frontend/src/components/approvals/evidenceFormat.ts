/** Formatting of evidence values for display. The values themselves are never changed. */

/** "52584.30000000" -> "52584.3": decimal strings arrive padded from the database; the value is unchanged. */
function trimNumber(s: string): string {
  return /^-?\d+\.\d+$/.test(s) ? s.replace(/0+$/, '').replace(/\.$/, '') : s;
}

/** "commissionPerLot" -> "Commission per lot" */
function humanKey(k: string): string {
  const words = k.replace(/_/g, ' ').replace(/([a-z])([A-Z])/g, '$1 $2').toLowerCase();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

export function show(v: unknown): string {
  if (v === null || v === undefined || v === '') return '—';
  if (Array.isArray(v)) return v.map(show).join(', ');
  if (typeof v === 'object')
    return Object.entries(v as Record<string, unknown>).map(([k, x]) => `${humanKey(k)}: ${show(x)}`).join(' · ') || '—';
  if (typeof v === 'number') return trimNumber(String(v));
  return trimNumber(String(v));
}
