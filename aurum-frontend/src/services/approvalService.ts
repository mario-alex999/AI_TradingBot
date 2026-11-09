import { apiGet, apiPost, apiPut } from './api';
import { resolveSource, type RequestOptions } from './source';
import type { Sourced } from '@/types/api';
import type {
  ApprovalDashboard,
  ApprovalDetail,
  ApprovalPage,
  ApprovalPolicyView,
  ApprovalRequest,
  ApproveBody,
  AuditRow,
  Evidence,
  ManagementAction,
  ManagementRequest,
  ModifyBody,
  PositionsView,
  RejectBody,
} from '@/types/approvals';

/**
 * Module 07 endpoints (/api/v1/approvals, /api/v1/trade-management). Every call needs a signed-in session; decisions
 * also need the CSRF token, which the API client adds. Nothing here places, modifies or closes an order: an approval
 * becomes an execution request that Module 09 validates again.
 *
 * There is deliberately no demo data: in demo mode every read is empty, so the app never shows an invented approval.
 */
const enc = encodeURIComponent;

export interface ApprovalFilters {
  status?: string;
  symbol?: string;
  direction?: string;
  timeframe?: string;
  risk?: string;
  sort?: string;
  page?: number;
  pageSize?: number;
}

export const getDashboard = (o: RequestOptions): Promise<Sourced<ApprovalDashboard | null>> =>
  resolveSource(o, () => null, (signal) => apiGet<ApprovalDashboard>('/approvals/dashboard', { signal }));

export const getPending = (o: RequestOptions): Promise<Sourced<ApprovalPage | null>> =>
  resolveSource(o, () => null, (signal) => apiGet<ApprovalPage>('/approvals/pending', { signal }));

export function listApprovals(o: RequestOptions, f: ApprovalFilters): Promise<Sourced<ApprovalPage | null>> {
  const params = Object.fromEntries(Object.entries(f).filter(([, v]) => v !== '' && v !== undefined));
  return resolveSource(o, () => null, (signal) => apiGet<ApprovalPage>('/approvals', { signal, params }));
}

export const getApproval = (o: RequestOptions, id: string): Promise<Sourced<ApprovalDetail | null>> =>
  resolveSource(o, () => null, (signal) => apiGet<ApprovalDetail>(`/approvals/${enc(id)}`, { signal }));

export const getEvidence = (o: RequestOptions, id: string): Promise<Sourced<Evidence | null>> =>
  resolveSource(o, () => null, (signal) => apiGet<Evidence>(`/approvals/${enc(id)}/evidence`, { signal }));

export const getHistory = (o: RequestOptions, id: string): Promise<Sourced<{ items: AuditRow[] } | null>> =>
  resolveSource(o, () => null, (signal) => apiGet<{ items: AuditRow[] }>(`/approvals/${enc(id)}/history`, { signal }));

export const getPolicy = (o: RequestOptions): Promise<Sourced<ApprovalPolicyView | null>> =>
  resolveSource(o, () => null, (signal) => apiGet<ApprovalPolicyView>('/approvals/policy', { signal }));

export const startReview = (id: string) => apiPost<ApprovalRequest>(`/approvals/${enc(id)}/review`);
export const approve = (id: string, body: ApproveBody) => apiPost<ApprovalRequest>(`/approvals/${enc(id)}/approve`, body);
export const reject = (id: string, body: RejectBody) => apiPost<ApprovalRequest>(`/approvals/${enc(id)}/reject`, body);
export const requestModification = (id: string, body: ModifyBody) =>
  apiPost<{ modificationId: string; status: string; newApproval?: ApprovalRequest }>(`/approvals/${enc(id)}/request-modification`, body);
export const cancelApproval = (id: string, reason: string) => apiPost<ApprovalRequest>(`/approvals/${enc(id)}/cancel`, { reason });
export const revalidateRisk = (id: string) => apiPost<ApprovalRequest>(`/approvals/${enc(id)}/revalidate-risk`);
export const syncIdeas = () => apiPost<{ ingested: number; closed: number; errors: number }>('/approvals/sync');
export const markApprovalNotificationsRead = (id?: string) => apiPost<{ marked: number }>('/approvals/notifications/read', id ? { id } : {});
export const updatePolicy = (policy: unknown, reason: string, expectedVersion: number) =>
  apiPut<ApprovalPolicyView>('/approvals/policy', { policy, reason, expectedVersion });

// ---- trade management -----------------------------------------------------------------------------------------------------
export const getPositions = (o: RequestOptions): Promise<Sourced<PositionsView | null>> =>
  resolveSource(o, () => null, (signal) => apiGet<PositionsView>('/trade-management/positions', { signal }));

export const getManagementRequests = (o: RequestOptions): Promise<Sourced<{ items: ManagementRequest[] } | null>> =>
  resolveSource(o, () => null, (signal) => apiGet<{ items: ManagementRequest[] }>('/trade-management/requests', { signal }));

export interface ManagementInput {
  positionId: string;
  action: ManagementAction;
  params: Record<string, string>;
  reason: string;
  clientRequestId: string;
}

export const createManagementRequest = (body: ManagementInput) => apiPost<ManagementRequest>('/trade-management/requests', body);
export const decideManagement = (id: string, decision: 'approve' | 'reject', body: Record<string, unknown>) =>
  apiPost<ManagementRequest>(`/trade-management/requests/${enc(id)}/${decision}`, body);

/** A random id for idempotent retries: the same click retried after a network error is applied once. */
export function newRequestId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') return crypto.randomUUID();
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}
