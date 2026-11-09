import { useQueries, useQuery } from '@tanstack/react-query';
import { useSettingsStore } from '@/stores/settingsStore';
import { fetchHealth } from '@/services/healthService';
import { getCandles, getInstruments, getQuotes } from '@/services/marketService';
import { getCollectionLogs, getDataQuality, getSpecifications, type LogQuery } from '@/services/dataService';
import {
  getCotAnalysis,
  getCotDivergence,
  getCotHistory,
  getCotInstruments,
  getCotLogs,
  getCotQuality,
  getCotReportDetail,
  getCotReports,
  getCotStatus,
  type CotQualityQuery,
} from '@/services/cotService';
import type { CotReportType } from '@/types/cot';
import { getMarketStructure, getOverlays, getSignals } from '@/services/analysisService';
import { getAccountSummary, getOpenTrades, getPerformance, getRiskOverview, getTradeHistory, type PerformanceFilters } from '@/services/tradingService';
import { getAgentStatus, getLearningLineage, getNotifications } from '@/services/systemService';
import type { Capability } from '@/types/system';
import {
  getCalendar,
  getEventRisk,
  getIntelligenceStatus,
  getMacroOverview,
  getMarketIntelligence,
  getNews,
  getSessionLevels,
  getSessionLiquidity,
  getSessionsNow,
  getVolatility,
  getNewsImpact,
} from '@/services/intelligenceService';
import type { CalendarQuery } from '@/types/intelligence';
import type { Timeframe } from '@/types/market';

/**
 * Query hooks. Pages consume these and never call services directly, so
 * switching between DEMO DATA and the live API requires no page changes —
 * the mock flag is part of every query key.
 */
const useMock = () => useSettingsStore((s) => s.mockMode);

export function useHealth() {
  return useQuery({
    queryKey: ['health'],
    queryFn: ({ signal }) => fetchHealth(signal),
    staleTime: 30_000,
    refetchInterval: 60_000, // single lightweight heartbeat; all other data is on-demand
    retry: 1,
  });
}

/** True only when the backend advertises the capability AND mock mode is off. */
export function useCapability(cap: Capability): boolean {
  const mock = useMock();
  const { data } = useHealth();
  return !mock && !!data?.capabilities.includes(cap);
}

export function useInstruments() {
  const mock = useMock();
  return useQuery({
    queryKey: ['instruments', mock],
    queryFn: ({ signal }) => getInstruments({ mock, signal }),
    staleTime: 5 * 60_000,
  });
}

export function useQuotes(symbols?: string[]) {
  const mock = useMock();
  return useQuery({
    queryKey: ['quotes', mock, symbols?.join(',') ?? 'all'],
    queryFn: ({ signal }) => getQuotes({ mock, signal }, symbols),
    staleTime: 15_000,
  });
}

export function useCandles(symbol: string, timeframe: Timeframe, limit = 500) {
  const mock = useMock();
  return useQuery({
    queryKey: ['candles', mock, symbol, timeframe, limit],
    queryFn: ({ signal }) => getCandles({ mock, signal }, symbol, timeframe, limit),
    staleTime: 60_000,
  });
}

/** Candles for several symbols at once (one cached query per symbol, same keys as useCandles). */
export function useCandlesMany(symbols: string[], timeframe: Timeframe, limit = 500) {
  const mock = useMock();
  return useQueries({
    queries: symbols.map((symbol) => ({
      queryKey: ['candles', mock, symbol, timeframe, limit],
      queryFn: ({ signal }: { signal: AbortSignal }) => getCandles({ mock, signal }, symbol, timeframe, limit),
      staleTime: 60_000,
    })),
  });
}

export function useSpecifications() {
  const mock = useMock();
  return useQuery({ queryKey: ['specs', mock], queryFn: ({ signal }) => getSpecifications({ mock, signal }) });
}

export function useDataQuality() {
  const mock = useMock();
  return useQuery({ queryKey: ['data-quality', mock], queryFn: ({ signal }) => getDataQuality({ mock, signal }) });
}

export function useCollectionLogs(q: LogQuery) {
  const mock = useMock();
  return useQuery({
    queryKey: ['collection-logs', mock, q],
    queryFn: ({ signal }) => getCollectionLogs({ mock, signal }, q),
    placeholderData: (prev) => prev,
  });
}

// ---- COT Institutional Intelligence Engine (Module 02). COT data changes weekly -> long stale times.
const COT_STALE = 10 * 60_000;

export function useCotStatus(probe = false) {
  const mock = useMock();
  return useQuery({ queryKey: ['cot-status', mock, probe], queryFn: ({ signal }) => getCotStatus({ mock, signal }, probe), staleTime: probe ? COT_STALE : 60_000 });
}

export function useCotInstruments() {
  const mock = useMock();
  return useQuery({ queryKey: ['cot-instruments', mock], queryFn: ({ signal }) => getCotInstruments({ mock, signal }), staleTime: COT_STALE });
}

export function useCotHistory(instrument: string, reportType: CotReportType) {
  const mock = useMock();
  return useQuery({
    queryKey: ['cot-history', mock, instrument, reportType],
    queryFn: ({ signal }) => getCotHistory({ mock, signal }, instrument, reportType),
    staleTime: COT_STALE,
  });
}

export function useCotAnalysisReport(instrument: string, asOf?: string) {
  const mock = useMock();
  return useQuery({
    queryKey: ['cot-analysis', mock, instrument, asOf ?? ''],
    queryFn: ({ signal }) => getCotAnalysis({ mock, signal }, instrument, asOf),
    staleTime: COT_STALE,
  });
}

export function useCotDivergence(instrument: string, reportType: CotReportType, category?: string) {
  const mock = useMock();
  return useQuery({
    queryKey: ['cot-divergence', mock, instrument, reportType, category ?? ''],
    queryFn: ({ signal }) => getCotDivergence({ mock, signal }, instrument, reportType, category),
    staleTime: COT_STALE,
  });
}

export function useCotQuality(q: CotQualityQuery) {
  const mock = useMock();
  return useQuery({ queryKey: ['cot-quality', mock, q], queryFn: ({ signal }) => getCotQuality({ mock, signal }, q), placeholderData: (prev) => prev });
}

export function useCotLogs(page = 1, pageSize = 10) {
  const mock = useMock();
  return useQuery({ queryKey: ['cot-logs', mock, page, pageSize], queryFn: ({ signal }) => getCotLogs({ mock, signal }, page, pageSize) });
}

export function useCotReports(q: { instrument: string; reportType: CotReportType; page: number; pageSize: number; start?: string; end?: string }) {
  const mock = useMock();
  return useQuery({ queryKey: ['cot-reports', mock, q], queryFn: ({ signal }) => getCotReports({ mock, signal }, q), placeholderData: (prev) => prev });
}

export function useCotReportDetail(id: number | null) {
  const mock = useMock();
  return useQuery({
    queryKey: ['cot-report', mock, id],
    queryFn: ({ signal }) => getCotReportDetail({ mock, signal }, id as number),
    enabled: id != null,
    staleTime: COT_STALE,
  });
}

export function useMarketStructure(instrument: string) {
  const mock = useMock();
  return useQuery({
    queryKey: ['structure', mock, instrument],
    queryFn: ({ signal }) => getMarketStructure({ mock, signal }, instrument),
  });
}

export function useOverlays(instrument: string, timeframe: Timeframe) {
  const mock = useMock();
  return useQuery({
    queryKey: ['overlays', mock, instrument, timeframe],
    queryFn: ({ signal }) => getOverlays({ mock, signal }, instrument, timeframe),
  });
}

export function useSignals() {
  const mock = useMock();
  return useQuery({ queryKey: ['signals', mock], queryFn: ({ signal }) => getSignals({ mock, signal }) });
}

export function useAccountSummary() {
  const mock = useMock();
  return useQuery({ queryKey: ['account', mock], queryFn: ({ signal }) => getAccountSummary({ mock, signal }) });
}

export function useOpenTrades() {
  const mock = useMock();
  return useQuery({ queryKey: ['trades-open', mock], queryFn: ({ signal }) => getOpenTrades({ mock, signal }) });
}

export function useTradeHistory() {
  const mock = useMock();
  return useQuery({ queryKey: ['trades-history', mock], queryFn: ({ signal }) => getTradeHistory({ mock, signal }) });
}

export function useRiskOverview() {
  const mock = useMock();
  return useQuery({ queryKey: ['risk', mock], queryFn: ({ signal }) => getRiskOverview({ mock, signal }) });
}

export function usePerformance(kind: 'live' | 'backtest', filters: PerformanceFilters = {}) {
  const mock = useMock();
  return useQuery({
    queryKey: ['performance', kind, mock, filters],
    queryFn: ({ signal }) => getPerformance({ mock, signal }, kind, filters),
  });
}

export function useAgents() {
  const mock = useMock();
  return useQuery({ queryKey: ['agents', mock], queryFn: ({ signal }) => getAgentStatus({ mock, signal }) });
}

export function useNotifications() {
  const mock = useMock();
  return useQuery({ queryKey: ['notifications', mock], queryFn: ({ signal }) => getNotifications({ mock, signal }) });
}

export function useLearningLineage() {
  const mock = useMock();
  return useQuery({ queryKey: ['learning', mock], queryFn: ({ signal }) => getLearningLineage({ mock, signal }), staleTime: 60_000 });
}

// ---------------------------------------------------------------- Module 04: market intelligence
const MI_STALE = 60_000;

export function useIntelligenceStatus() {
  const mock = useMock();
  return useQuery({ queryKey: ['mi-status', mock], queryFn: ({ signal }) => getIntelligenceStatus({ mock, signal }), staleTime: MI_STALE });
}

export function useBackendSessions(symbol?: string) {
  const mock = useMock();
  return useQuery({
    queryKey: ['mi-sessions', mock, symbol ?? ''],
    queryFn: ({ signal }) => getSessionsNow({ mock, signal }, symbol),
    staleTime: 30_000,
    refetchInterval: 60_000,
  });
}

export function useSessionLevels(symbol: string) {
  const mock = useMock();
  return useQuery({ queryKey: ['mi-levels', mock, symbol], queryFn: ({ signal }) => getSessionLevels({ mock, signal }, symbol), staleTime: MI_STALE });
}

export function useSessionLiquidity(symbol: string) {
  const mock = useMock();
  return useQuery({ queryKey: ['mi-liquidity', mock, symbol], queryFn: ({ signal }) => getSessionLiquidity({ mock, signal }, symbol), staleTime: MI_STALE });
}

export function useEconomicCalendar(q: CalendarQuery) {
  const mock = useMock();
  return useQuery({
    queryKey: ['mi-calendar', mock, q],
    queryFn: ({ signal }) => getCalendar({ mock, signal }, q),
    staleTime: MI_STALE,
    placeholderData: (prev) => prev,
  });
}

export function useEventRisk(symbol: string) {
  const mock = useMock();
  return useQuery({ queryKey: ['mi-risk', mock, symbol], queryFn: ({ signal }) => getEventRisk({ mock, signal }, symbol), staleTime: 30_000 });
}

export function useMacroOverview() {
  const mock = useMock();
  return useQuery({ queryKey: ['mi-macro', mock], queryFn: ({ signal }) => getMacroOverview({ mock, signal }), staleTime: 5 * 60_000 });
}

export function useMarketNews(symbol?: string, page = 1) {
  const mock = useMock();
  return useQuery({
    queryKey: ['mi-news', mock, symbol ?? '', page],
    queryFn: ({ signal }) => getNews({ mock, signal }, symbol, page),
    staleTime: 5 * 60_000,
    placeholderData: (prev) => prev,
  });
}

export function useVolatility(symbol: string) {
  const mock = useMock();
  return useQuery({ queryKey: ['mi-volatility', mock, symbol], queryFn: ({ signal }) => getVolatility({ mock, signal }, symbol), staleTime: MI_STALE });
}

export function useMarketIntelligence(symbol: string) {
  const mock = useMock();
  return useQuery({
    queryKey: ['mi-market', mock, symbol],
    queryFn: ({ signal }) => getMarketIntelligence({ mock, signal }, symbol),
    staleTime: MI_STALE,
  });
}

export function useNewsImpact(q: { importance: string; hoursAhead: number; currency?: string }) {
  const mock = useMock();
  return useQuery({
    queryKey: ['news-impact', mock, q.importance, q.hoursAhead, q.currency ?? ''],
    queryFn: ({ signal }) => getNewsImpact({ mock, signal }, q),
    refetchInterval: 60_000, // outcomes and measured moves update after a release
  });
}
