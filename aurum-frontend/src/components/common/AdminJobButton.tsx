import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { RefreshCw } from 'lucide-react';
import { Button } from '@/components/common/Button';
import { useAdminGate } from '@/hooks/useDecision';
import { useAdminKeyStore } from '@/stores/adminKeyStore';
import { toast } from '@/stores/toastStore';
import { describeError } from '@/utils/errors';
import { ADMIN_ACTIONS, getAdminJob, startAdminJob, type AdminActionId } from '@/services/adminJobService';

const DONE = ['succeeded', 'failed'];

interface Props {
  action: AdminActionId;
  /** Query keys (first element) to refresh when the job succeeds. */
  invalidate: string[];
  label?: string;
  variant?: 'primary' | 'secondary';
}

/**
 * Starts a backend admin job (data refresh, recalculation, decision run) with the admin key, follows it until it
 * finishes, then refreshes the affected panels. Disabled with the reason in demo mode, without permission or key.
 */
export function AdminJobButton({ action, invalidate, label, variant = 'secondary' }: Props) {
  const gate = useAdminGate();
  const key = useAdminKeyStore((s) => s.key);
  const qc = useQueryClient();
  const [jobId, setJobId] = useState<string | null>(null);
  const text = label ?? ADMIN_ACTIONS[action].label;

  const start = useMutation({
    mutationFn: () => startAdminJob(action, key),
    onSuccess: (job) => {
      setJobId(job.jobId);
      toast({ tone: 'info', title: `${text} started`, description: 'Running on the backend. This page updates when it finishes.' });
    },
    onError: (e) => toast({ tone: 'error', title: `${text} not started`, description: describeError(e).description }),
  });

  const job = useQuery({
    queryKey: ['admin-job', action, jobId],
    queryFn: ({ signal }) => getAdminJob(action, jobId ?? '', signal),
    enabled: !!jobId,
    refetchInterval: (q) => (DONE.includes(q.state.data?.status ?? '') ? false : 3_000),
  });

  const status = job.data?.status;
  useEffect(() => {
    if (!jobId || !status || !DONE.includes(status)) return;
    if (status === 'succeeded') {
      toast({ tone: 'success', title: `${text} finished`, description: job.data?.progress?.at(-1) });
      for (const k of invalidate) void qc.invalidateQueries({ queryKey: [k] });
    } else {
      toast({ tone: 'error', title: `${text} failed`, description: job.data?.error ?? 'See the server log.' });
    }
    setJobId(null);
  }, [jobId, status, job.data, invalidate, qc, text]);

  const busy = start.isPending || (!!jobId && !DONE.includes(status ?? ''));
  return (
    <Button
      size="sm"
      variant={variant}
      icon={<RefreshCw className={busy ? 'h-3.5 w-3.5 animate-spin' : 'h-3.5 w-3.5'} aria-hidden />}
      loading={false}
      disabled={!gate.allowed || busy}
      disabledReason={busy ? 'Running…' : gate.reason}
      onClick={() => start.mutate()}
      title="Admin action: runs on the backend with the admin key"
    >
      {busy ? `${text}…` : text}
    </Button>
  );
}
