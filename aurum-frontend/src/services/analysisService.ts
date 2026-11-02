import { apiGet } from './api';
import { resolveSource, type RequestOptions } from './source';
import type { MarketStructureReport } from '@/types/analysis';
import type { TradeSignal } from '@/types/trading';
import type { ChartOverlay, Timeframe } from '@/types/market';
import type { Sourced } from '@/types/api';

/**
 * Analysis outputs (structure, SMC/ICT overlays, signals) have NO demo data:
 * the frontend must never fabricate them. In mock mode these return empty.
 */
export function getMarketStructure(opts: RequestOptions, instrument: string): Promise<Sourced<MarketStructureReport | null>> {
  return resolveSource(
    opts,
    () => null,
    (signal) => apiGet<MarketStructureReport>('/analysis/market-structure', { signal, params: { instrument } }),
  );
}

export function getOverlays(opts: RequestOptions, instrument: string, timeframe: Timeframe): Promise<Sourced<ChartOverlay[]>> {
  return resolveSource(
    opts,
    () => [],
    (signal) => apiGet<ChartOverlay[]>('/analysis/market-structure', { signal, params: { instrument, timeframe, view: 'overlays' } }),
  );
}

export function getSignals(opts: RequestOptions): Promise<Sourced<TradeSignal[]>> {
  return resolveSource(
    opts,
    () => [],
    (signal) => apiGet<TradeSignal[]>('/analysis/signals', { signal }),
  );
}
