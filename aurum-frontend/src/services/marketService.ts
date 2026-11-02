import { apiGet } from './api';
import { resolveSource, type RequestOptions } from './source';
import { demoCandles, demoInstruments, demoQuotes } from '@/mocks/market';
import type { Candle, Instrument, Quote, Timeframe } from '@/types/market';
import type { Sourced } from '@/types/api';

export function getInstruments(opts: RequestOptions): Promise<Sourced<Instrument[]>> {
  return resolveSource(opts, demoInstruments, (signal) => apiGet<Instrument[]>('/market/instruments', { signal }));
}

export function getQuotes(opts: RequestOptions, symbols?: string[]): Promise<Sourced<Quote[]>> {
  return resolveSource(
    opts,
    () => demoQuotes(symbols),
    (signal) => apiGet<Quote[]>('/market/quotes', { signal, params: symbols ? { symbols: symbols.join(',') } : undefined }),
  );
}

export function getCandles(
  opts: RequestOptions,
  symbol: string,
  timeframe: Timeframe,
  limit = 500,
): Promise<Sourced<Candle[]>> {
  return resolveSource(
    opts,
    () => demoCandles(symbol, timeframe, limit),
    (signal) => apiGet<Candle[]>('/market/candles', { signal, params: { symbol, timeframe, limit } }),
  );
}
