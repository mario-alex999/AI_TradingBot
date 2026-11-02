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
import { usePerformance } from '@/hooks/queries';
import { DEFAULT_INSTRUMENTS, TIMEFRAMES } from '@/config/instruments';

const schema = z
  .object({
    strategy: z.string().min(1, 'Select a strategy'),
    instrument: z.string().min(1),
    timeframe: z.enum(['D1', 'H4', 'H1', 'M15', 'M5']),
    from: z.string().min(1, 'Required'),
    to: z.string().min(1, 'Required'),
    capital: z.coerce.number().positive(),
    riskPct: z.coerce.number().gt(0).max(10),
    commission: z.coerce.number().min(0),
    spread: z.coerce.number().min(0),
    slippage: z.coerce.number().min(0),
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

export default function BacktestingPage() {
  const results = usePerformance('backtest');
  const {
    register,
    formState: { errors },
  } = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: { strategy: '', instrument: 'EURUSD', timeframe: 'H1', from: '2023-01-01', to: '2025-12-31', capital: 10000, riskPct: 1, commission: 7, spread: 1, slippage: 0.5 },
  });

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
          <form className="grid gap-4 sm:grid-cols-2 xl:grid-cols-5" onSubmit={(e) => e.preventDefault()} noValidate>
            <Field id="bt-strategy" label="Strategy" error={errors.strategy?.message}>
              <select id="bt-strategy" className="field" {...register('strategy')}>
                <option value="">No strategies registered</option>
              </select>
            </Field>
            <Field id="bt-inst" label="Instrument">
              <select id="bt-inst" className="field" {...register('instrument')}>
                {DEFAULT_INSTRUMENTS.map((i) => (
                  <option key={i.symbol}>{i.symbol}</option>
                ))}
              </select>
            </Field>
            <Field id="bt-tf" label="Timeframe">
              <select id="bt-tf" className="field" {...register('timeframe')}>
                {TIMEFRAMES.map((t) => (
                  <option key={t}>{t}</option>
                ))}
              </select>
            </Field>
            <Field id="bt-from" label="From" error={errors.from?.message}>
              <input id="bt-from" type="date" className="field num" {...register('from')} />
            </Field>
            <Field id="bt-to" label="To" error={errors.to?.message}>
              <input id="bt-to" type="date" className="field num" {...register('to')} />
            </Field>
            <Field id="bt-capital" label="Initial capital (USD)" error={errors.capital?.message}>
              <input id="bt-capital" type="number" className="field num" {...register('capital')} />
            </Field>
            <Field id="bt-risk" label="Risk per trade (%)" error={errors.riskPct?.message}>
              <input id="bt-risk" type="number" step="0.1" className="field num" {...register('riskPct')} />
            </Field>
            <Field id="bt-comm" label="Commission per lot (USD)" error={errors.commission?.message}>
              <input id="bt-comm" type="number" step="0.1" className="field num" {...register('commission')} />
            </Field>
            <Field id="bt-spread" label="Spread assumption (pips)" error={errors.spread?.message}>
              <input id="bt-spread" type="number" step="0.1" className="field num" {...register('spread')} />
            </Field>
            <Field id="bt-slip" label="Slippage assumption (pips)" error={errors.slippage?.message}>
              <input id="bt-slip" type="number" step="0.1" className="field num" {...register('slippage')} />
            </Field>
            <div className="flex items-end sm:col-span-2 xl:col-span-5">
              <Button variant="primary" icon={<Play className="h-4 w-4" aria-hidden />} disabled disabledReason="Backtesting engine endpoint not available yet">
                Run backtest
              </Button>
              <span className="ml-3 text-2xs text-neutral">Enabled once the Strategy Evaluation Agent exposes a backtest endpoint.</span>
            </div>
          </form>
        </CardBody>
      </Card>
      <PerformanceView report={results.data?.data ?? null} kind="backtest" emptyDescription="No backtest has been run. Results will never be estimated or simulated in the browser." />
    </div>
  );
}
