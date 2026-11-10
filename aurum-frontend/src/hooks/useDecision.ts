import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useAdminKeyStore } from '@/stores/adminKeyStore';
import { useSettingsStore } from '@/stores/settingsStore';
import { toast } from '@/stores/toastStore';
import { usePermission } from '@/hooks/usePermission';
import { describeError } from '@/utils/errors';
import {
  getAiHealth,
  getDossier,
  getEvaluation,
  getEvaluations,
  getJob,
  getLatestAnalysis,
  getProviders,
  getSetups,
  getStrategies,
  getTradeIdeas,
  reviewSignal,
  runAnalysis,
  runBacktest,
  type BacktestStart,
  getDecisionStatus,
} from '@/services/decisionService';
import type { ReviewAction, ReviewInput } from '@/types/decision';

const useMock = () => useSettingsStore((s) => s.mockMode);

export function useDossier(id: string | null) {
  const mock = useMock();
  return useQuery({ queryKey: ['dossier', mock, id], queryFn: ({ signal }) => getDossier({ mock, signal }, id ?? ''), enabled: !!id });
}

export function useLatestAnalysis(symbol: string) {
  const mock = useMock();
  return useQuery({ queryKey: ['analysis-latest', mock, symbol], queryFn: ({ signal }) => getLatestAnalysis({ mock, signal }, symbol) });
}

/** A scan rebuilds the analysis on the backend, so it runs only when asked. */
export function useSetups(symbol: string, enabled: boolean) {
  const mock = useMock();
  return useQuery({ queryKey: ['setups', mock, symbol], queryFn: ({ signal }) => getSetups({ mock, signal }, symbol), enabled, staleTime: 60_000 });
}

export function useTradeIdeas() {
  const mock = useMock();
  return useQuery({ queryKey: ['trade-ideas', mock], queryFn: ({ signal }) => getTradeIdeas({ mock, signal }) });
}

export function useStrategies() {
  const mock = useMock();
  return useQuery({ queryKey: ['strategies', mock], queryFn: ({ signal }) => getStrategies({ mock, signal }), staleTime: 5 * 60_000 });
}

export function useProviders() {
  const mock = useMock();
  return useQuery({ queryKey: ['ai-providers', mock], queryFn: ({ signal }) => getProviders({ mock, signal }) });
}

export function useAiHealth() {
  const mock = useMock();
  return useQuery({ queryKey: ['ai-health', mock], queryFn: ({ signal }) => getAiHealth({ mock, signal }) });
}

export function useEvaluations() {
  const mock = useMock();
  return useQuery({ queryKey: ['evaluations', mock], queryFn: ({ signal }) => getEvaluations({ mock, signal }) });
}

export function useEvaluation(id: string | null) {
  const mock = useMock();
  return useQuery({ queryKey: ['evaluation', mock, id], queryFn: ({ signal }) => getEvaluation({ mock, signal }, id ?? ''), enabled: !!id });
}

export const JOB_DONE = ['succeeded', 'failed'];

export function useJob(id: string | null) {
  return useQuery({
    queryKey: ['decision-job', id],
    queryFn: ({ signal }) => getJob(id ?? '', signal),
    enabled: !!id,
    refetchInterval: (q) => (JOB_DONE.includes(q.state.data?.status ?? '') ? false : 5_000),
  });
}

export interface AdminGate {
  allowed: boolean;
  /** Why the action is unavailable; undefined when it is allowed. */
  reason?: string;
}

/** Whether this session can use an admin endpoint. UI only: the backend authorises every call. */
export function useAdminGate(): AdminGate {
  const key = useAdminKeyStore((s) => s.key);
  const mock = useMock();
  const permitted = usePermission('signals:review');
  if (mock) return { allowed: false, reason: 'Unavailable in demo-data mode' };
  if (!permitted) return { allowed: false, reason: 'Your role cannot review signals' };
  if (!key) return { allowed: false, reason: 'Enter the admin key in Settings → Security' };
  return { allowed: true };
}

const DONE: Record<ReviewAction, string> = { approve: 'Approval recorded', reject: 'Rejection recorded', reopen: 'Candidate reopened' };

export function useReviewSignal(onDone?: () => void) {
  const qc = useQueryClient();
  const key = useAdminKeyStore((s) => s.key);
  return useMutation({
    mutationFn: ({ action, id, body }: { action: ReviewAction; id: string; body: ReviewInput }) => reviewSignal(action, id, body, key),
    onSuccess: (_r, v) => {
      toast({ tone: 'success', title: DONE[v.action], description: 'No order was sent. AURUM AI does not place trades.' });
      for (const k of ['signals', 'dossier', 'trade-ideas', 'account']) void qc.invalidateQueries({ queryKey: [k] });
      onDone?.();
    },
    onError: (e) => toast({ tone: 'error', title: 'Review not recorded', description: describeError(e).description }),
  });
}

export function useRunAnalysis() {
  const qc = useQueryClient();
  const key = useAdminKeyStore((s) => s.key);
  return useMutation({
    mutationFn: (symbol: string) => runAnalysis(symbol, true, key),
    onSuccess: (_r, symbol) => {
      toast({ tone: 'success', title: `${symbol} analysis finished` });
      for (const k of ['analysis-latest', 'signals', 'trade-ideas', 'ai-health', 'ai-providers']) void qc.invalidateQueries({ queryKey: [k] });
    },
    onError: (e) => toast({ tone: 'error', title: 'Analysis failed', description: describeError(e).description }),
  });
}

export function useRunBacktest() {
  const key = useAdminKeyStore((s) => s.key);
  return useMutation({
    mutationFn: (body: BacktestStart) => runBacktest(body, key),
    onSuccess: (r) => toast({ tone: 'info', title: 'Backtest started', description: `Job ${r.jobId}` }),
    onError: (e) => toast({ tone: 'error', title: 'Backtest not started', description: describeError(e).description }),
  });
}

export function useDecisionStatus() {
  const mock = useMock();
  return useQuery({ queryKey: ['decision-status', mock], queryFn: ({ signal }) => getDecisionStatus({ mock, signal }), staleTime: 30_000 });
}
