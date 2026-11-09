import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useAdminKeyStore } from '@/stores/adminKeyStore';
import { useSettingsStore } from '@/stores/settingsStore';
import { toast } from '@/stores/toastStore';
import { usePermission } from '@/hooks/usePermission';
import { describeError } from '@/utils/errors';
import {
  assessTrade,
  calculatePositionSize,
  createRiskLock,
  getAssessment,
  getRiskAccount,
  getRiskConfig,
  getRiskHealth,
  getRiskHistory,
  getRiskLocks,
  getRiskPortfolio,
  releaseRiskLock,
  updateRiskConfig,
  type LockInput,
} from '@/services/riskService';
import type { ConfigUpdate, HistoryFilters, TradeProposalInput } from '@/types/risk';

const useMock = () => useSettingsStore((s) => s.mockMode);
/** Account and exposure move with the market; refresh while the page is open. */
const LIVE_MS = 30_000;
const RISK_KEYS = ['risk-account', 'risk-portfolio', 'risk-locks', 'risk-history', 'risk-config', 'risk-health'];

export function useRiskAccount() {
  const mock = useMock();
  return useQuery({ queryKey: ['risk-account', mock], queryFn: ({ signal }) => getRiskAccount({ mock, signal }), refetchInterval: LIVE_MS });
}

export function useRiskPortfolio() {
  const mock = useMock();
  return useQuery({ queryKey: ['risk-portfolio', mock], queryFn: ({ signal }) => getRiskPortfolio({ mock, signal }), refetchInterval: LIVE_MS });
}

export function useRiskLocks() {
  const mock = useMock();
  return useQuery({ queryKey: ['risk-locks', mock], queryFn: ({ signal }) => getRiskLocks({ mock, signal }), refetchInterval: LIVE_MS });
}

export function useRiskConfig() {
  const mock = useMock();
  return useQuery({ queryKey: ['risk-config', mock], queryFn: ({ signal }) => getRiskConfig({ mock, signal }) });
}

export function useRiskHealth() {
  const mock = useMock();
  return useQuery({ queryKey: ['risk-health', mock], queryFn: ({ signal }) => getRiskHealth({ mock, signal }), refetchInterval: 60_000 });
}

export function useRiskHistory(filters: HistoryFilters) {
  const mock = useMock();
  return useQuery({ queryKey: ['risk-history', mock, filters], queryFn: ({ signal }) => getRiskHistory({ mock, signal }, filters) });
}

export function useAssessment(id: string | null) {
  const mock = useMock();
  return useQuery({ queryKey: ['risk-assessment', mock, id], queryFn: ({ signal }) => getAssessment({ mock, signal }, id ?? ''), enabled: !!id });
}

export interface RiskAdminGate {
  allowed: boolean;
  reason?: string;
}

/** Whether this session may send a Module 06 admin request. UI only: the backend authorises every call. */
export function useRiskAdminGate(): RiskAdminGate {
  const key = useAdminKeyStore((s) => s.key);
  const mock = useMock();
  const permitted = usePermission('risk:manage');
  if (mock) return { allowed: false, reason: 'Unavailable in demo-data mode' };
  if (!permitted) return { allowed: false, reason: 'Your role cannot change risk controls' };
  if (!key) return { allowed: false, reason: 'Enter the admin key in Settings → Security' };
  return { allowed: true };
}

function useInvalidateRisk() {
  const qc = useQueryClient();
  return () => RISK_KEYS.forEach((k) => void qc.invalidateQueries({ queryKey: [k] }));
}

/** What-if sizing; no admin key needed and nothing is stored. */
export function usePositionSize() {
  return useMutation({ mutationFn: (body: TradeProposalInput) => calculatePositionSize(body) });
}

export function useAssessTrade() {
  const key = useAdminKeyStore((s) => s.key);
  const invalidate = useInvalidateRisk();
  return useMutation({
    mutationFn: (body: TradeProposalInput) => assessTrade(body, key),
    onSuccess: () => invalidate(),
    onError: (e) => toast({ tone: 'error', title: 'Assessment not run', description: describeError(e).description }),
  });
}

/** Errors are handled by the caller: a weakening change needs an explicit second confirmation. */
export function useUpdateRiskConfig() {
  const key = useAdminKeyStore((s) => s.key);
  const invalidate = useInvalidateRisk();
  return useMutation({
    mutationFn: (body: ConfigUpdate) => updateRiskConfig(body, key),
    onSuccess: (r) => {
      toast({ tone: 'success', title: `Risk settings saved as version ${r.version}`, description: 'Existing locks are unchanged.' });
      invalidate();
    },
  });
}

export function useCreateLock(onDone?: () => void) {
  const key = useAdminKeyStore((s) => s.key);
  const invalidate = useInvalidateRisk();
  return useMutation({
    mutationFn: (body: LockInput) => createRiskLock(body, key),
    onSuccess: () => {
      toast({ tone: 'success', title: 'Trading lock activated', description: 'New assessments in its scope are blocked until it is released.' });
      invalidate();
      onDone?.();
    },
    onError: (e) => toast({ tone: 'error', title: 'Lock not activated', description: describeError(e).description }),
  });
}

export function useReleaseLock(onDone?: () => void) {
  const key = useAdminKeyStore((s) => s.key);
  const invalidate = useInvalidateRisk();
  return useMutation({
    mutationFn: ({ id, reason }: { id: string; reason: string }) => releaseRiskLock(id, reason, key),
    onSuccess: (r) => {
      toast({ tone: 'success', title: 'Lock released', description: r.note });
      invalidate();
      onDone?.();
    },
    onError: (e) => toast({ tone: 'error', title: 'Lock not released', description: describeError(e).description }),
  });
}
