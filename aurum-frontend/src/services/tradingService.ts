import { apiGet } from './api';
import { resolveSource, type RequestOptions } from './source';
import type { AccountSummary, PerformanceReport, Position } from '@/types/trading';
import type { Sourced } from '@/types/api';

/**
 * Read-only trading data. There are intentionally no order-placement or
 * position-modification calls anywhere in the frontend.
 *
 * Account balances, positions and performance are never mocked: mock mode
 * returns "no data" so nothing fabricated can be mistaken for a real figure.
 */
export function getAccountSummary(opts: RequestOptions): Promise<Sourced<AccountSummary | null>> {
  return resolveSource(opts, () => null, (signal) => apiGet<AccountSummary>('/account/summary', { signal }));
}

export function getOpenTrades(opts: RequestOptions): Promise<Sourced<Position[]>> {
  return resolveSource(opts, () => [], (signal) => apiGet<Position[]>('/trades/open', { signal }));
}

export function getTradeHistory(opts: RequestOptions): Promise<Sourced<Position[]>> {
  return resolveSource(opts, () => [], (signal) => apiGet<Position[]>('/trades/history', { signal }));
}

export type PerformanceFilters = Partial<Record<'from' | 'to' | 'strategy' | 'instrument' | 'timeframe' | 'session', string>>;

export function getPerformance(
  opts: RequestOptions,
  kind: 'live' | 'backtest',
  filters: PerformanceFilters = {},
): Promise<Sourced<PerformanceReport | null>> {
  const params = Object.fromEntries(Object.entries(filters).filter(([, v]) => v));
  return resolveSource(
    opts,
    () => null,
    (signal) => apiGet<PerformanceReport>(kind === 'live' ? '/performance/summary' : '/backtests/latest', { signal, params }),
  );
}
