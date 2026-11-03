/**
 * Runtime configuration read from Vite environment variables.
 * Only non-secret values belong here — every VITE_* value ships to the browser.
 */
const bool = (value: string | undefined, fallback: boolean): boolean =>
  value === undefined || value === '' ? fallback : value.toLowerCase() === 'true';

export const env = {
  apiBaseUrl: (import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000/api/v1').replace(/\/+$/, ''),
  mockDataDefault: bool(import.meta.env.VITE_ENABLE_MOCK_DATA, true),
  requestTimeoutMs: Number(import.meta.env.VITE_API_TIMEOUT_MS ?? 10_000) || 10_000,
  realtimeUrl: import.meta.env.VITE_REALTIME_URL?.trim() || null,
  /** Name shown in the "Built by" credit (sidebar and Settings → About). */
  builtBy: import.meta.env.VITE_BUILT_BY?.trim() || 'the AURUM AI engineering team',
} as const;

/**
 * The initial release is strictly read-only. No component may offer order
 * placement or position modification while this is true.
 */
export const READ_ONLY = true;
