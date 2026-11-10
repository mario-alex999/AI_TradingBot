import { apiGet, apiPost, apiPut } from './api';
import { resolveSource, type RequestOptions } from './source';
import type { Sourced } from '@/types/api';
import type {
  AuditRow,
  ConfigView,
  DeadLetter,
  Delivery,
  Execution,
  ExecutionDetail,
  Integration,
  OrchEvent,
  Overview,
  Page,
  Workflow,
  WorkflowVersion,
} from '@/types/orchestration';

/**
 * Module 08 endpoints (/api/v1/orchestration). Every read needs a signed-in session; actions also need the CSRF token,
 * which the API client adds. Module 08 coordinates n8n workflows only: nothing here approves, sizes or executes a trade.
 *
 * There is deliberately no demo data: in demo mode every read is empty, so the page never shows invented executions.
 */
const enc = encodeURIComponent;
const clean = (o: Record<string, unknown>) => Object.fromEntries(Object.entries(o).filter(([, v]) => v !== '' && v !== undefined && v !== null));

const read = <T,>(o: RequestOptions, path: string, params?: Record<string, unknown>): Promise<Sourced<T | null>> =>
  resolveSource(o, () => null, (signal) => apiGet<T>(path, { signal, params: params ? clean(params) : undefined }));

export const getOverview = (o: RequestOptions) => read<Overview>(o, '/orchestration/overview');
export const listWorkflows = (o: RequestOptions) => read<{ items: Workflow[] }>(o, '/orchestration/workflows');
export const listVersions = (o: RequestOptions, id: string) => read<{ items: WorkflowVersion[] }>(o, `/orchestration/workflows/${enc(id)}/versions`);

export interface ExecutionFilters {
  workflowId?: string;
  status?: string;
  symbol?: string;
  correlationId?: string;
  page?: number;
  pageSize?: number;
}
export const listExecutions = (o: RequestOptions, f: ExecutionFilters) => read<Page<Execution>>(o, '/orchestration/executions', { ...f });
export const getExecution = (o: RequestOptions, id: string) => read<ExecutionDetail>(o, `/orchestration/executions/${enc(id)}`);

export interface EventFilters {
  eventType?: string;
  source?: string;
  status?: string;
  correlationId?: string;
  page?: number;
  pageSize?: number;
}
export const listEvents = (o: RequestOptions, f: EventFilters) => read<Page<OrchEvent>>(o, '/orchestration/events', { ...f });
export const getDeliveries = (o: RequestOptions, id: string) => read<{ items: Delivery[] }>(o, `/orchestration/events/${enc(id)}/deliveries`);

export const listDeadLetters = (o: RequestOptions, status: string) =>
  read<Page<DeadLetter>>(o, '/orchestration/dead-letters', { status, pageSize: 100 });
export const listIntegrations = (o: RequestOptions) => read<{ items: Integration[] }>(o, '/orchestration/integrations');
export const getConfig = (o: RequestOptions) => read<ConfigView>(o, '/orchestration/config');
export const getAudit = (o: RequestOptions, action?: string) =>
  read<{ items: AuditRow[]; chain: { valid: boolean; rows: number; error: string | null } }>(o, '/orchestration/audit-logs', { action, limit: 200 });

// ---- actions (all audited server-side against the signed-in person) ---------------------------------------------------------
export const triggerTest = (workflowId: string, input: Record<string, unknown>) =>
  apiPost<Execution & { duplicate: boolean }>('/orchestration/executions/trigger', { workflowId, triggerType: 'manual', input });
export const retryExecution = (id: string, reason: string, confirmUnknownOutcome: boolean) =>
  apiPost<ExecutionDetail>(`/orchestration/executions/${enc(id)}/retry`, { reason, confirmUnknownOutcome });
export const cancelExecution = (id: string, reason: string) => apiPost<ExecutionDetail>(`/orchestration/executions/${enc(id)}/cancel`, { reason });
export const setWorkflowState = (id: string, state: string, reason: string) =>
  apiPost<Workflow>(`/orchestration/workflows/${enc(id)}/state`, { state, reason });
export const replayEvent = (id: string, reason: string) =>
  apiPost<{ replay: number; deliveries: number }>(`/orchestration/events/${enc(id)}/replay`, { reason });
export const retryDeadLetter = (id: string, reason: string) => apiPost<unknown>(`/orchestration/dead-letters/${enc(id)}/retry`, { reason });
export const resolveDeadLetter = (id: string, note: string) => apiPost<DeadLetter>(`/orchestration/dead-letters/${enc(id)}/resolve`, { note });
export const testIntegration = (id: string) => apiPost<Integration>(`/orchestration/integrations/${enc(id)}/test`);
export const updateConfig = (config: Record<string, unknown>, reason: string, version: number) =>
  apiPut<ConfigView>('/orchestration/config', { config, reason, version });
