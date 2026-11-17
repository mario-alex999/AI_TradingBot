/**
 * Module 05 reports each input's availability. The API sends an object per input
 * (`{ available: false, reason: "..." }`); older payloads and the mocks send a word ("available", "missing").
 * Both shapes are accepted here, so the screens never have to guess.
 */
export type AvailabilityEntry = string | { available?: boolean | null; reason?: string | null; [k: string]: unknown } | null;

export interface MissingInput {
  key: string;
  reason: string | null;
}

function isAvailable(v: AvailabilityEntry | undefined): boolean {
  if (v === null || v === undefined) return false;
  if (typeof v === 'string') return v === 'available';
  return v.available === true;
}

function reasonOf(v: AvailabilityEntry | undefined): string | null {
  if (v === null || v === undefined) return null;
  if (typeof v === 'string') return v;
  return typeof v.reason === 'string' && v.reason.trim() ? v.reason : null;
}

/** The inputs that were not available, with the reason the backend gave (or null). */
export function missingInputs(availability: Record<string, AvailabilityEntry> | null | undefined): MissingInput[] {
  return Object.entries(availability ?? {})
    .filter(([, v]) => !isAvailable(v))
    .map(([key, v]) => ({ key, reason: reasonOf(v) }));
}
