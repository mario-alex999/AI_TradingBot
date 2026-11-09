import { useEffect } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { env } from '@/config/env';
import { useAuthStore } from '@/stores/authStore';
import { useSettingsStore } from '@/stores/settingsStore';
import {
  approve,
  cancelApproval,
  createManagementRequest,
  decideManagement,
  getApproval,
  getDashboard,
  getEvidence,
  getHistory,
  getManagementRequests,
  getPending,
  getPolicy,
  getPositions,
  listApprovals,
  reject,
  requestModification,
  revalidateRisk,
  startReview,
  syncIdeas,
  type ApprovalFilters,
  type ManagementInput,
} from '@/services/approvalService';
import type { ApproveBody, ModifyBody, RejectBody } from '@/types/approvals';

/** Reads need a server session; in the demo workspace (or demo data mode) every query is empty rather than invented. */
function useOpts() {
  const mockMode = useSettingsStore((s) => s.mockMode);
  const server = useAuthStore((s) => s.user?.mode === 'server');
  return { mock: mockMode || !server, server };
}

const POLL_MS = 15_000;

export function useApprovalDashboard() {
  const { mock } = useOpts();
  return useQuery({ queryKey: ['approvals', 'dashboard', mock], queryFn: ({ signal }) => getDashboard({ mock, signal }), refetchInterval: POLL_MS });
}

export function usePendingApprovals() {
  const { mock } = useOpts();
  return useQuery({ queryKey: ['approvals', 'pending', mock], queryFn: ({ signal }) => getPending({ mock, signal }), refetchInterval: POLL_MS });
}

export function useApprovalList(filters: ApprovalFilters) {
  const { mock } = useOpts();
  return useQuery({ queryKey: ['approvals', 'list', mock, filters], queryFn: ({ signal }) => listApprovals({ mock, signal }, filters) });
}

export function useApprovalDetail(id: string | undefined) {
  const { mock } = useOpts();
  return useQuery({
    queryKey: ['approvals', 'detail', mock, id],
    queryFn: ({ signal }) => getApproval({ mock, signal }, id ?? ''),
    enabled: !!id,
    refetchInterval: POLL_MS,
  });
}

export function useApprovalEvidence(id: string | undefined, snapshotId: string | null | undefined) {
  const { mock } = useOpts();
  return useQuery({
    // keyed by snapshot: a revalidation produces a new snapshot, which must be reviewed again
    queryKey: ['approvals', 'evidence', mock, id, snapshotId],
    queryFn: ({ signal }) => getEvidence({ mock, signal }, id ?? ''),
    enabled: !!id,
  });
}

export function useApprovalHistory(id: string | undefined) {
  const { mock } = useOpts();
  return useQuery({ queryKey: ['approvals', 'history', mock, id], queryFn: ({ signal }) => getHistory({ mock, signal }, id ?? ''), enabled: !!id });
}

export function useApprovalPolicy() {
  const { mock } = useOpts();
  return useQuery({ queryKey: ['approvals', 'policy', mock], queryFn: ({ signal }) => getPolicy({ mock, signal }) });
}

function useInvalidate() {
  const qc = useQueryClient();
  return () => {
    void qc.invalidateQueries({ queryKey: ['approvals'] });
    void qc.invalidateQueries({ queryKey: ['management'] });
    void qc.invalidateQueries({ queryKey: ['notifications'] });
  };
}

/** Decisions. No optimistic updates: the screen shows the server's answer, never an assumed approval. */
export function useApprovalActions(id: string) {
  const done = useInvalidate();
  return {
    review: useMutation({ mutationFn: () => startReview(id), onSuccess: done }),
    approve: useMutation({ mutationFn: (b: ApproveBody) => approve(id, b), onSettled: done }),
    reject: useMutation({ mutationFn: (b: RejectBody) => reject(id, b), onSettled: done }),
    modify: useMutation({ mutationFn: (b: ModifyBody) => requestModification(id, b), onSettled: done }),
    cancel: useMutation({ mutationFn: (reason: string) => cancelApproval(id, reason), onSettled: done }),
    revalidate: useMutation({ mutationFn: () => revalidateRisk(id), onSettled: done }),
  };
}

export function useSyncIdeas() {
  const done = useInvalidate();
  return useMutation({ mutationFn: syncIdeas, onSuccess: done });
}

export function usePositions() {
  const { mock } = useOpts();
  return useQuery({ queryKey: ['management', 'positions', mock], queryFn: ({ signal }) => getPositions({ mock, signal }), refetchInterval: 30_000 });
}

export function useManagementRequests() {
  const { mock } = useOpts();
  return useQuery({ queryKey: ['management', 'requests', mock], queryFn: ({ signal }) => getManagementRequests({ mock, signal }), refetchInterval: POLL_MS });
}

export function useManagementActions() {
  const done = useInvalidate();
  return {
    create: useMutation({ mutationFn: (b: ManagementInput) => createManagementRequest(b), onSettled: done }),
    decide: useMutation({
      mutationFn: ({ id, decision, body }: { id: string; decision: 'approve' | 'reject'; body: Record<string, unknown> }) => decideManagement(id, decision, body),
      onSettled: done,
    }),
  };
}

/**
 * Live updates: the backend streams Module 07 outbox events (server-sent events). Any event refreshes the approval
 * views; polling stays on as a fallback. The stream carries no decision power: it only tells the page to reload.
 */
export function useApprovalStream(): void {
  const { mock, server } = useOpts();
  const qc = useQueryClient();
  useEffect(() => {
    if (mock || !server || typeof EventSource === 'undefined') return;
    const src = new EventSource(`${env.apiBaseUrl}/approvals/events/stream`, { withCredentials: true });
    const refresh = () => {
      void qc.invalidateQueries({ queryKey: ['approvals'] });
      void qc.invalidateQueries({ queryKey: ['management'] });
      void qc.invalidateQueries({ queryKey: ['notifications'] });
    };
    src.onmessage = refresh;
    const types = [
      'approval.requested', 'approval.under_review', 'approval.approved', 'approval.rejected', 'approval.expired', 'approval.cancelled',
      'approval.invalidated', 'approval.modification_requested', 'approval.risk_revalidated', 'trade.approved_for_execution',
      'execution.completed', 'execution.failed', 'trade_management.requested', 'trade_management.approved', 'trade_management.rejected',
      'trade_management.expired',
    ];
    types.forEach((t) => src.addEventListener(t, refresh));
    return () => src.close();
  }, [mock, server, qc]);
}
