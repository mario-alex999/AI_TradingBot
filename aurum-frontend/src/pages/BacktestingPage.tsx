import { useEffect, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { z } from 'zod';
import { FlaskConical, Play } from 'lucide-react';
import { PageHeader } from '@/components/common/PageHeader';
import { Card, CardBody, CardHeader } from '@/components/common/Card';
import { Button } from '@/components/common/Button';
import { Badge } from '@/components/common/Badge';
import { DataSourceBadge } from '@/components/common/DataSourceBadge';
import { PerformanceView } from '@/components/analytics/PerformanceView';
import { EvaluationDetails } from '@/components/ai/EvaluationDetails';
import { usePerformance } from '@/hooks/queries';
import { JOB_DONE, useAdminGate, useJob, useRunBacktest, useStrategies } from '@/hooks/useDecision';
import { DEFAULT_INSTRUMENTS } from '@/config/instruments';
import { humanize } from '@/utils/labels';
import type { BacktestReport } from '@/types/decision';

const schema = z
  .object({
    strategy: z.string(),
    instrument: z.string(),
    from: z.string().min(1, 'Required'),
    to: z.string().min(1, 'Required'),
  })
  .refine((v) => v.from < v.to, { message: 'Start must be before end', path: ['to'] });
type Values = z.infer<typeof schema>;

function Field({ id, label, error, children }: { id: string; label: string; error?: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={id} className="text-xs text-ink-muted">
        {label}
      </label>
      {children}
      {error && <span className="text-2xs text-down-text">{error}</span>}
    </div>
  );
}

const isoDay = (daysAgo: number) => new Date(Date.now() - daysAgo * 86_400_000).toISOString().slice(0, 10);

export default function BacktestingPage() {
  const qc = useQueryClient();
  const results = usePerformance('backtest');
  const strategies = useStrategies();
  const gate = useAdminGate();
  const start = useRunBacktest();
  const [jobId, setJobId] = useState<string | null>(null);
  const job = useJob(jobId);
  const jobStatus = job.data?.status;
  const details = (results.data?.data as BacktestReport | null | undefined)?.details;
  const {
    register,
    handleSubmit,
    formState: { errors },
  } = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: { strategy: '', instrument: 'EURUSD', from: isoDay(14), to: isoDay(0) },
  });

  useEffect(() => {
    if (jobStatus && JOB_DONE.includes(jobStatus)) void qc.invalidateQueries({ queryKey: ['performance', 'backtest'] });
  }, [jobStatus, qc]);

  const submit = handleSubmit((v) =>
    start.mutate({ from: v.from, to: v.to, ...(v.instrument ? { symbols: [v.instrument] } : {}), ...(v.strategy ? { template: v.strategy } : {}) }, { onSuccess: (r) => setJobId(r.jobId) }),
  );
  const running = !!jobId && !(jobStatus && JOB_DONE.includes(jobStatus));

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        eyebrow="Research"
        title="Backtesting"
        description="Historical simulation of strategies. Backtest results are simulations and are always shown separately from live performance."
        actions={<DataSourceBadge source={results.data?.source} />}
      />
      <Card>
        <CardHeader
          icon={<FlaskConical className="h-4 w-4" />}
          title="Backtest configuration"
          actions={<Badge tone="info">Simulation</Badge>}
        />
        <CardBody>
          <form className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4" onSubmit={(e) => void submit(e)} noValidate>
            <Field id="bt-strategy" label="Strategy" error={errors.strategy?.message}>
              <select id="bt-strategy" className="field" {...register('strategy')}>
                <option value="">{strategies.data?.data.length ? 'All enabled strategies' : 'No strategies registered'}</option>
                {strategies.data?.data.map((s) => (
                  <option key={s.id} value={s.id}>
                    {humanize(s.id)}
                  </option>
                ))}
              </select>
            </Field>
            <Field id="bt-inst" label="Instrument">
              <select id="bt-inst" className="field" {...register('instrument')}>
                <option value="">All instruments</option>
                {DEFAULT_INSTRUMENTS.map((i) => (
                  <option key={i.symbol}>{i.symbol}</option>
                ))}
              </select>
            </Field>
            <Field id="bt-from" label="From" error={errors.from?.message}>
              <input id="bt-from" type="date" className="field num" {...register('from')} />
            </Field>
            <Field id="bt-to" label="To" error={errors.to?.message}>
              <input id="bt-to" type="date" className="field num" {...register('to')} />
            </Field>
            <div className="flex items-end sm:col-span-2 xl:col-span-4">
              <Button type="submit" variant="primary" icon={<Play className="h-4 w-4" aria-hidden />} loading={start.isPending || running} disabled={!gate.allowed} disabledReason={gate.reason}>
                Run backtest
              </Button>
              <span className="ml-3 text-2xs text-neutral">
                {jobId ? `Job ${jobId}: ${jobStatus ?? 'queued'}${job.data?.error ? ` (${job.data.error})` : ''}` : 'A replay rebuilds the full analysis at every step and can take a long time. Costs and risk come from config/ai_engine.yaml.'}
              </span>
            </div>
          </form>
        </CardBody>
      </Card>
      <PerformanceView report={results.data?.data ?? null} kind="backtest" emptyDescription="No backtest has been run. Results will never be estimated or simulated in the browser." />
      {details && <EvaluationDetails d={details} />}
    </div>
  );
}
