import { apiGet } from './api';
import { resolveSource, type RequestOptions } from './source';
import { demoAgents, demoNotifications } from '@/mocks/system';
import type { AgentInfo, AppNotification } from '@/types/system';
import type { Sourced } from '@/types/api';

export function getAgentStatus(opts: RequestOptions): Promise<Sourced<AgentInfo[]>> {
  return resolveSource(opts, demoAgents, (signal) => apiGet<AgentInfo[]>('/agents/status', { signal }));
}

export function getNotifications(opts: RequestOptions): Promise<Sourced<AppNotification[]>> {
  return resolveSource(opts, demoNotifications, (signal) => apiGet<AppNotification[]>('/notifications', { signal }));
}
