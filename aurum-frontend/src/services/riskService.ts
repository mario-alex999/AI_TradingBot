import { apiGet, apiPost, apiPut } from './api';
import { resolveSource, type RequestOptions } from './source';
import type { Sourced } from '@/types/api';
import type {
  Assessment,
  AssessmentPage,
  ConfigUpdate,
  HistoryFilters,
  PositionSizeResult,
  RiskAccount,
  RiskConfigView,
  RiskHealth,
  RiskLock,
  RiskLocks,
  RiskPortfolio,
  TradeProposalInput,
} from '@/types/risk';

/**
 * Module 06 endpoints (/api/v1/risk). The browser only displays what the risk engine decides and sends
 * administrative requests (settings, locks) that the backend authorises with the admin key.
 * No endpoint here can place, modify or close an order.
 *
 * There is deliberately no demo data: in mock mode every read returns empty, so the dashboard never shows
 * an invented balance, drawdown or approval.
 */
const ADMIN_HEADER = 'X-AURUM-Admin-Key';
const admin = (key: string) => ({ headers: { [ADMIN_HEADER]: key } });

export function getRiskAccount(opts: RequestOptions): Promise<Sourced<RiskAccount | null>> {
  return resolveSource(opts, () => null, (signal) => apiGet<RiskAccount>('/risk/account', { signal }));
}

export function getRiskPortfolio(opts: RequestOptions): Promise<Sourced<RiskPortfolio | null>> {
  return resolveSource(opts, () => null, (signal) => apiGet<RiskPortfolio>('/risk/portfolio', { signal }));
}

export function getRiskLocks(opts: RequestOptions): Promise<Sourced<RiskLocks | null>> {
  return resolveSource(opts, () => null, (signal) => apiGet<RiskLocks>('/risk/locks', { signal }));
}

export function getRiskConfig(opts: RequestOptions): Promise<Sourced<RiskConfigView | null>> {
  return resolveSource(opts, () => null, (signal) => apiGet<RiskConfigView>('/risk/config', { signal }));
}

export function getRiskHealth(opts: RequestOptions): Promise<Sourced<RiskHealth | null>> {
  return resolveSource(opts, () => null, (signal) => apiGet<RiskHealth>('/risk/health', { signal }));
}

export function getRiskHistory(opts: RequestOptions, f: HistoryFilters): Promise<Sourced<AssessmentPage | null>> {
  const params = Object.fromEntries(Object.entries(f).filter(([, v]) => v !== '' && v !== undefined));
  return resolveSource(opts, () => null, (signal) => apiGet<AssessmentPage>('/risk/history', { signal, params }));
}

export function getAssessment(opts: RequestOptions, id: string): Promise<Sourced<Assessment | null>> {
  return resolveSource(opts, () => null, (signal) => apiGet<Assessment>(`/risk/assessments/${encodeURIComponent(id)}`, { signal }));
}

/** What-if sizing. Open endpoint: nothing is reserved or stored. */
export function calculatePositionSize(body: TradeProposalInput): Promise<PositionSizeResult> {
  return apiPost<PositionSizeResult>('/risk/position-size', body);
}

/** A full assessment (all rules, reservations, audit). Admin only; it records a decision, never an order. */
export function assessTrade(body: TradeProposalInput, key: string): Promise<Assessment> {
  return apiPost<Assessment>('/risk/assess', body, admin(key));
}

export function updateRiskConfig(body: ConfigUpdate, key: string): Promise<RiskConfigView> {
  return apiPut<RiskConfigView>('/risk/config', body, admin(key));
}

export interface LockInput {
  scope: RiskLock['scope'];
  scopeValue?: string;
  reason: string;
}

export function createRiskLock(body: LockInput, key: string): Promise<RiskLock> {
  return apiPost<RiskLock>('/risk/locks', body, admin(key));
}

export function releaseRiskLock(id: string, reason: string, key: string): Promise<{ note?: string }> {
  return apiPost(`/risk/locks/${encodeURIComponent(id)}/release`, { reason }, admin(key));
}
