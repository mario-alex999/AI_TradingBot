import { apiGet, apiPost } from './api';
import { resolveSource, type RequestOptions } from './source';
import type { Sourced } from '@/types/api';
import type {
  AiHealth,
  AnalysisDetail,
  AnalysisRow,
  BacktestReport,
  EvaluationRun,
  JobState,
  ProvidersView,
  ReviewAction,
  ReviewInput,
  SetupScan,
  SignalDossier,
  StrategyRow,
  TradeIdea,
} from '@/types/decision';

/**
 * Module 05 endpoints. Every one is display-only or records a human decision; none can place a trade.
 * Nothing here has demo data: in mock mode each returns empty, so the UI never shows an invented setup.
 */
const ADMIN_HEADER = 'X-AURUM-Admin-Key';
/** A live scan or analysis reads every timeframe on the backend, which outlasts the default timeout. */
const SLOW_MS = 180_000;

const admin = (key: string, timeout?: number) => ({ headers: { [ADMIN_HEADER]: key }, ...(timeout ? { timeout } : {}) });

export function getDossier(opts: RequestOptions, id: string): Promise<Sourced<SignalDossier | null>> {
  return resolveSource(
    opts,
    () => null,
    (signal) => apiGet<SignalDossier>(`/analysis/signals/${encodeURIComponent(id)}`, { signal }),
  );
}

export function reviewSignal(action: ReviewAction, id: string, body: ReviewInput, key: string): Promise<unknown> {
  return apiPost(`/analysis/signals/${encodeURIComponent(id)}/${action}`, body, admin(key));
}

export function getLatestAnalysis(opts: RequestOptions, symbol: string): Promise<Sourced<AnalysisDetail | null>> {
  return resolveSource(
    opts,
    () => null,
    async (signal) => {
      const rows = await apiGet<AnalysisRow[]>('/ai/analysis', { signal, params: { symbol, limit: 1 } });
      return rows[0] ? apiGet<AnalysisDetail>(`/ai/analysis/${encodeURIComponent(rows[0].id)}`, { signal }) : null;
    },
  );
}

export function runAnalysis(symbol: string, aiReview: boolean, key: string): Promise<unknown> {
  return apiPost('/ai/analyze', { symbol, aiReview }, admin(key, SLOW_MS));
}

export function getSetups(opts: RequestOptions, symbol: string): Promise<Sourced<SetupScan | null>> {
  return resolveSource(
    opts,
    () => null,
    (signal) => apiGet<SetupScan>(`/ai/setups/${encodeURIComponent(symbol)}`, { signal, timeout: SLOW_MS }),
  );
}

export function getTradeIdeas(opts: RequestOptions): Promise<Sourced<TradeIdea[]>> {
  return resolveSource(
    opts,
    () => [],
    (signal) => apiGet<TradeIdea[]>('/ai/trade-ideas', { signal }),
  );
}

export function getStrategies(opts: RequestOptions): Promise<Sourced<StrategyRow[]>> {
  return resolveSource(
    opts,
    () => [],
    (signal) => apiGet<StrategyRow[]>('/ai/strategies', { signal }),
  );
}

export function getProviders(opts: RequestOptions): Promise<Sourced<ProvidersView | null>> {
  return resolveSource(
    opts,
    () => null,
    (signal) => apiGet<ProvidersView>('/ai/providers', { signal }),
  );
}

export function getAiHealth(opts: RequestOptions): Promise<Sourced<AiHealth | null>> {
  return resolveSource(
    opts,
    () => null,
    (signal) => apiGet<AiHealth>('/ai/health', { signal }),
  );
}

export function getEvaluations(opts: RequestOptions): Promise<Sourced<EvaluationRun[]>> {
  return resolveSource(
    opts,
    () => [],
    (signal) => apiGet<EvaluationRun[]>('/ai/evaluation/runs', { signal }),
  );
}

export function getEvaluation(opts: RequestOptions, id: string): Promise<Sourced<BacktestReport | null>> {
  return resolveSource(
    opts,
    () => null,
    (signal) => apiGet<BacktestReport>(`/ai/evaluation/${encodeURIComponent(id)}`, { signal }),
  );
}

export function runBacktest(body: BacktestStart, key: string): Promise<{ jobId: string; status: string }> {
  return apiPost('/backtests/run', body, admin(key));
}

export interface BacktestStart {
  from: string;
  to: string;
  symbols?: string[];
  template?: string;
}

export function getJob(id: string, signal?: AbortSignal): Promise<JobState> {
  return apiGet<JobState>(`/decision/jobs/${encodeURIComponent(id)}`, { signal });
}
