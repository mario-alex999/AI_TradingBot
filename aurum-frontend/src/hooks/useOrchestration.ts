import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useAuthStore } from '@/stores/authStore';
import { useSettingsStore } from '@/stores/settingsStore';
import {
  cancelExecution,
  getAudit,
  getConfig,
  getDeliveries,
  getExecution,
  getOverview,
  listDeadLetters,
  listEvents,
  listExecutions,
  listIntegrations,
  listVersions,
  listWorkflows,
  replayEvent,
  resolveDeadLetter,
  retryDeadLetter,
  retryExecution,
  setWorkflowState,
  testIntegration,
  triggerTest,
  updateConfig,
  type EventFilters,
  type ExecutionFilters,
} from '@/services/orchestrationService';

/** Reads need a server session; in the demo workspace (or demo data mode) every query is empty rather than invented. */
function useMock() {
  const mockMode = useSettingsStore((s) => s.mockMode);
  const server = useAuthStore((s) => s.user?.mode === 'server');
  return mockMode || !server;
}

const POLL = 10_000;
const K = 'orchestration';

export function useOrchOverview() {
  const mock = useMock();
  return useQuery({ queryKey: [K, 'overview', mock], queryFn: ({ signal }) => getOverview({ mock, signal }), refetchInterval: POLL });
}
export function useWorkflows() {
  const mock = useMock();
  return useQuery({ queryKey: [K, 'workflows', mock], queryFn: ({ signal }) => listWorkflows({ mock, signal }), refetchInterval: 30_000 });
}
export function useWorkflowVersions(id: string | null) {
  const mock = useMock();
  return useQuery({ queryKey: [K, 'versions', mock, id], queryFn: ({ signal }) => listVersions({ mock, signal }, id ?? ''), enabled: !!id });
}
export function useExecutions(f: ExecutionFilters) {
  const mock = useMock();
  return useQuery({ queryKey: [K, 'executions', mock, f], queryFn: ({ signal }) => listExecutions({ mock, signal }, f), refetchInterval: POLL });
}
export function useExecution(id: string | null) {
  const mock = useMock();
  return useQuery({ queryKey: [K, 'execution', mock, id], queryFn: ({ signal }) => getExecution({ mock, signal }, id ?? ''), enabled: !!id, refetchInterval: POLL });
}
export function useOrchEvents(f: EventFilters) {
  const mock = useMock();
  return useQuery({ queryKey: [K, 'events', mock, f], queryFn: ({ signal }) => listEvents({ mock, signal }, f), refetchInterval: POLL });
}
export function useDeliveries(id: string | null) {
  const mock = useMock();
  return useQuery({ queryKey: [K, 'deliveries', mock, id], queryFn: ({ signal }) => getDeliveries({ mock, signal }, id ?? ''), enabled: !!id });
}
export function useDeadLetters(status: string) {
  const mock = useMock();
  return useQuery({ queryKey: [K, 'dead-letters', mock, status], queryFn: ({ signal }) => listDeadLetters({ mock, signal }, status), refetchInterval: POLL });
}
export function useIntegrations() {
  const mock = useMock();
  return useQuery({ queryKey: [K, 'integrations', mock], queryFn: ({ signal }) => listIntegrations({ mock, signal }), refetchInterval: 30_000 });
}
export function useOrchConfig() {
  const mock = useMock();
  return useQuery({ queryKey: [K, 'config', mock], queryFn: ({ signal }) => getConfig({ mock, signal }) });
}
export function useOrchAudit(action: string) {
  const mock = useMock();
  return useQuery({ queryKey: [K, 'audit', mock, action], queryFn: ({ signal }) => getAudit({ mock, signal }, action || undefined) });
}

/** Every action refreshes all Module 08 queries: the server is the only source of truth. */
function useAction<A, R>(fn: (a: A) => Promise<R>) {
  const qc = useQueryClient();
  return useMutation({ mutationFn: fn, onSettled: () => void qc.invalidateQueries({ queryKey: [K] }) });
}

export const useTriggerTest = () => useAction((a: { workflowId: string; input: Record<string, unknown> }) => triggerTest(a.workflowId, a.input));
export const useRetryExecution = () =>
  useAction((a: { id: string; reason: string; confirmUnknownOutcome: boolean }) => retryExecution(a.id, a.reason, a.confirmUnknownOutcome));
export const useCancelExecution = () => useAction((a: { id: string; reason: string }) => cancelExecution(a.id, a.reason));
export const useSetWorkflowState = () => useAction((a: { id: string; state: string; reason: string }) => setWorkflowState(a.id, a.state, a.reason));
export const useReplayEvent = () => useAction((a: { id: string; reason: string }) => replayEvent(a.id, a.reason));
export const useRetryDeadLetter = () => useAction((a: { id: string; reason: string }) => retryDeadLetter(a.id, a.reason));
export const useResolveDeadLetter = () => useAction((a: { id: string; note: string }) => resolveDeadLetter(a.id, a.note));
export const useTestIntegration = () => useAction((id: string) => testIntegration(id));
export const useUpdateConfig = () =>
  useAction((a: { config: Record<string, unknown>; reason: string; version: number }) => updateConfig(a.config, a.reason, a.version));
