import { apiGet } from './api';
import { resolveSource, type RequestOptions } from './source';
import { demoAgents, demoNotifications } from '@/mocks/system';
import type { AgentInfo, AppNotification } from '@/types/system';
import type { Sourced } from '@/types/api';
import type { LearningLineage } from '@/types/learning';

export function getAgentStatus(opts: RequestOptions): Promise<Sourced<AgentInfo[]>> {
  return resolveSource(opts, demoAgents, (signal) => apiGet<AgentInfo[]>('/agents/status', { signal }));
}

export function getNotifications(opts: RequestOptions): Promise<Sourced<AppNotification[]>> {
  return resolveSource(opts, demoNotifications, (signal) => apiGet<AppNotification[]>('/notifications', { signal }));
}

/**
 * GET /agents/learning — the Trade Learning Agent's lineage. Never mocked: agent results are
 * performance, and performance is not simulated. Demo mode returns null (the panel explains the cycle).
 */
export function getLearningLineage(opts: RequestOptions): Promise<Sourced<LearningLineage | null>> {
  return resolveSource(
    opts,
    () => null,
    async (signal) => (await apiGet<{ lineage: LearningLineage | null }>('/agents/learning', { signal })).lineage,
  );
}
