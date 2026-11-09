import { apiGet } from './api';
import { resolveSource, type RequestOptions } from './source';
import type { Sourced } from '@/types/api';
import type {
  CalendarPage,
  CalendarQuery,
  EventRisk,
  IntelligenceStatus,
  MacroOverview,
  MarketIntelligence,
  NewsPage,
  SessionLevels,
  SessionLiquidity,
  SessionsSnapshot,
  VolatilityReport,
} from '@/types/intelligence';
import type { NewsImpactReport } from '@/types/newsImpact';

/**
 * Module 04 market intelligence. Like the Module 03 analysis outputs, this has NO demo
 * data: economic events, central-bank rates, headlines and market conditions must never
 * be fabricated. In mock mode every call resolves to `null` and panels show "unavailable".
 */
const P = '/intelligence';

const live = <T>(opts: RequestOptions, path: string, params?: Record<string, unknown>): Promise<Sourced<T | null>> =>
  resolveSource<T | null>(opts, () => null, (signal) => apiGet<T>(`${P}${path}`, { signal, params }));

export const getIntelligenceStatus = (opts: RequestOptions) => live<IntelligenceStatus>(opts, '/status');

export const getSessionsNow = (opts: RequestOptions, symbol?: string) =>
  live<SessionsSnapshot>(opts, '/sessions/current', symbol ? { symbol } : undefined);

export const getSessionLevels = (opts: RequestOptions, symbol: string) => live<SessionLevels>(opts, `/sessions/levels/${symbol}`);

export const getSessionLiquidity = (opts: RequestOptions, symbol: string) =>
  live<SessionLiquidity>(opts, `/sessions/liquidity/${symbol}`);

export function getCalendar(opts: RequestOptions, q: CalendarQuery): Promise<Sourced<CalendarPage | null>> {
  return live<CalendarPage>(opts, '/calendar', {
    range: q.range,
    currency: q.currencies?.length ? q.currencies.join(',') : undefined,
    importance: q.importance?.length ? q.importance.join(',') : undefined,
    category: q.categories?.length ? q.categories.join(',') : undefined,
    instrument: q.instrument || undefined,
    page: q.page ?? 1,
    pageSize: q.pageSize ?? 50,
  });
}

export const getEventRisk = (opts: RequestOptions, symbol: string) => live<EventRisk>(opts, `/calendar/impact/${symbol}`);

export const getMacroOverview = (opts: RequestOptions) => live<MacroOverview>(opts, '/macro/overview');

export const getNews = (opts: RequestOptions, symbol?: string, page = 1, pageSize = 20) =>
  live<NewsPage>(opts, '/news', { symbol: symbol || undefined, page, pageSize });

export const getVolatility = (opts: RequestOptions, symbol: string) => live<VolatilityReport>(opts, `/volatility/${symbol}`);

export const getMarketIntelligence = (opts: RequestOptions, symbol: string) =>
  live<MarketIntelligence>(opts, `/market/${symbol}`, { detail: 'full' });

/** High-impact events with conditional scenarios, measured past reactions and outcomes, plus headlines. */
export const getNewsImpact = (opts: RequestOptions, q: { importance: string; hoursAhead: number; currency?: string }) =>
  live<NewsImpactReport>(opts, '/news-impact', { importance: q.importance, hoursAhead: q.hoursAhead, ...(q.currency ? { currency: q.currency } : {}) });
