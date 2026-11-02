import type { Sourced } from '@/types/api';

export interface RequestOptions {
  /** true = return the DEMO DATA layer, never touching the network. */
  mock: boolean;
  signal?: AbortSignal;
}

/**
 * Chooses between the clearly-separated demo layer and the live backend.
 * There is deliberately NO silent fallback from live to demo: if the backend
 * fails, the error surfaces so the UI can report the disconnection honestly.
 */
export async function resolveSource<T>(
  opts: RequestOptions,
  demo: () => T | Promise<T>,
  live: (signal?: AbortSignal) => Promise<T>,
): Promise<Sourced<T>> {
  if (opts.mock) {
    const data = await demo();
    return { data, source: 'demo', fetchedAt: new Date().toISOString() };
  }
  const data = await live(opts.signal);
  return { data, source: 'live', fetchedAt: new Date().toISOString() };
}
