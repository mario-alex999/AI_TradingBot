import { useForm, useWatch } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { z } from 'zod';
import { Calculator } from 'lucide-react';
import { Card, CardBody, CardHeader } from '@/components/common/Card';
import { formatCurrency, formatNumber } from '@/utils/format';
import { calculatePositionSize } from '@/utils/risk';
import { cn } from '@/utils/cn';

const schema = z.object({
  balance: z.coerce.number().positive('Must be positive'),
  riskPct: z.coerce.number().gt(0).max(10, 'Max 10%'),
  stopPoints: z.coerce.number().positive('Must be positive'),
  pointValue: z.coerce.number().positive('Must be positive'),
});
type Values = z.infer<typeof schema>;


const FIELDS: { name: keyof Values; label: string; step: string }[] = [
  { name: 'balance', label: 'Account balance (USD)', step: '100' },
  { name: 'riskPct', label: 'Risk per trade (%)', step: '0.1' },
  { name: 'stopPoints', label: 'Stop distance (points)', step: '1' },
  { name: 'pointValue', label: 'Value per point per lot (USD)', step: '0.01' },
];

// Typical value of one point per 1.0 lot and a sample stop distance, per instrument.
const PRESETS = [
  { symbol: 'EURUSD', pointValue: 1, stop: 200 },
  { symbol: 'GBPUSD', pointValue: 1, stop: 250 },
  { symbol: 'XAUUSD', pointValue: 1, stop: 1500 },
  { symbol: 'US30', pointValue: 1, stop: 800 },
];

/** Informational calculator. It is NOT a risk control — limits are enforced by the backend. */
export function PositionSizeCalculator() {
  const {
    register,
    control,
    setValue,
    formState: { errors },
  } = useForm<Values>({ resolver: zodResolver(schema), mode: 'onChange', defaultValues: { balance: 10000, riskPct: 1, stopPoints: 200, pointValue: 1 } });
  const values = useWatch({ control });
  const parsed = schema.safeParse(values);
  const result = parsed.success ? calculatePositionSize(parsed.data) : null;

  return (
    <Card>
      <CardHeader icon={<Calculator className="h-4 w-4" />} title="Position sizing calculator" subtitle="Informational only — not a security control. The backend enforces all limits." />
      <CardBody className="grid gap-4 md:grid-cols-[minmax(0,1fr)_220px]">
        <form className="grid gap-3 sm:grid-cols-2" onSubmit={(e) => e.preventDefault()} noValidate>
          <div className="flex flex-col gap-2 sm:col-span-2">
            <div className="flex items-center justify-between">
              <label htmlFor="ps-risk-slider" className="text-xs text-ink-muted">
                Risk per trade
              </label>
              <span className="num text-sm font-semibold text-ink">{Number(values.riskPct ?? 0).toFixed(2)}%</span>
            </div>
            <input
              id="ps-risk-slider"
              type="range"
              min={0.1}
              max={5}
              step={0.05}
              value={Number(values.riskPct ?? 1)}
              onChange={(e) => setValue('riskPct', Number(e.target.value), { shouldValidate: true })}
              className="w-full accent-gold"
            />
            <div className="flex flex-wrap gap-1.5" role="group" aria-label="Risk presets">
              {[0.25, 0.5, 1, 2].map((r) => (
                <button
                  key={r}
                  type="button"
                  aria-pressed={Number(values.riskPct) === r}
                  onClick={() => setValue('riskPct', r, { shouldValidate: true })}
                  className={cn(
                    'rounded-full border px-3 py-1 text-xs transition',
                    Number(values.riskPct) === r ? 'border-gold bg-gold text-white' : 'border-line-control text-ink hover:border-ink/40',
                  )}
                >
                  {r}%
                </button>
              ))}
            </div>
          </div>
          <div className="flex flex-col gap-2 sm:col-span-2">
            <span className="text-xs text-ink-muted">Instrument preset (value per point per lot, typical)</span>
            <div className="flex flex-wrap gap-1.5" role="group" aria-label="Instrument presets">
              {PRESETS.map((p) => (
                <button
                  key={p.symbol}
                  type="button"
                  onClick={() => {
                    setValue('pointValue', p.pointValue, { shouldValidate: true });
                    setValue('stopPoints', p.stop, { shouldValidate: true });
                  }}
                  className="rounded-full border border-line-control px-3 py-1 text-xs text-ink transition hover:border-ink/40 active:scale-95"
                >
                  {p.symbol}
                </button>
              ))}
            </div>
            <span className="text-2xs text-neutral">Presets are typical contract values. Check Data Centre → Instrument configuration for your broker's tick value.</span>
          </div>
          {FIELDS.map((f) => (
            <div key={f.name} className="flex flex-col gap-1">
              <label htmlFor={`ps-${f.name}`} className="text-xs text-ink-muted">
                {f.label}
              </label>
              <input id={`ps-${f.name}`} type="number" step={f.step} className="field num" aria-invalid={!!errors[f.name]} {...register(f.name)} />
              {errors[f.name] && <span className="text-2xs text-down-text">{errors[f.name]?.message}</span>}
            </div>
          ))}
        </form>
        <div className="flex flex-col justify-center gap-3 rounded-md border border-line-soft bg-base-raised p-4" aria-live="polite">
          <div>
            <p className="eyebrow">Amount at risk</p>
            <p className="num mt-1 text-lg font-semibold text-ink">{result ? formatCurrency(result.riskAmount) : '—'}</p>
          </div>
          <div>
            <p className="eyebrow">Position size</p>
            <p className="num mt-1 text-2xl text-ink" data-testid="lots">
              {result ? `${formatNumber(result.lots, 2)} lots` : '—'}
            </p>
          </div>
        </div>
      </CardBody>
    </Card>
  );
}
