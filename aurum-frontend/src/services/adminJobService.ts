import { apiGet, apiPost } from './api';

/** A background job on the backend (all modules share one job manager). */
export interface BackendJob {
  jobId: string;
  type: string;
  status: 'queued' | 'running' | 'succeeded' | 'failed';
  progress?: string[];
  result?: Record<string, unknown> | null;
  error?: string | null;
}

const ADMIN_HEADER = 'X-AURUM-Admin-Key';
const SLOW_MS = 180_000;

/** Admin data actions across modules. Each starts a job (or runs synchronously) and says where to poll it. */
export const ADMIN_ACTIONS = {
  calendarRefresh: { path: '/intelligence/calendar/refresh', poll: '/intelligence/jobs', body: {}, label: 'Refresh calendar' },
  newsRefresh: { path: '/intelligence/news/refresh', poll: '/intelligence/jobs', body: undefined, label: 'Refresh news' },
  macroRefresh: { path: '/intelligence/macro/refresh', poll: '/intelligence/jobs', body: undefined, label: 'Refresh macro data' },
  cotUpdate: { path: '/cot/update', poll: '/cot/jobs', body: {}, label: 'Update COT data' },
  decisionRunAll: { path: '/decision/run', poll: '/decision/jobs', body: { aiReview: true }, label: 'Run all instruments' },
} as const;
export type AdminActionId = keyof typeof ADMIN_ACTIONS;

export function startAdminJob(id: AdminActionId, key: string): Promise<BackendJob> {
  const a = ADMIN_ACTIONS[id];
  return apiPost<BackendJob>(a.path, a.body, { headers: { [ADMIN_HEADER]: key }, timeout: SLOW_MS });
}

export function getAdminJob(id: AdminActionId, jobId: string, signal?: AbortSignal): Promise<BackendJob> {
  return apiGet<BackendJob>(`${ADMIN_ACTIONS[id].poll}/${encodeURIComponent(jobId)}`, { signal });
}

/** Module 03: analyse one instrument across timeframes and persist it (synchronous, returns the confluence summary). */
export function runStructureAnalysis(symbol: string, key: string): Promise<{ symbol: string; bias: string; score: number; grade: string }> {
  return apiPost('/analysis/run', { symbol }, { headers: { [ADMIN_HEADER]: key }, timeout: SLOW_MS });
}
