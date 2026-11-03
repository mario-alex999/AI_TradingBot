import { useForm, useWatch } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { z } from 'zod';
import { Calculator } from 'lucide-react';
import { Card, CardBody, CardHeader } from '@/components/common/Card';
import { formatCurrency, formatNumber } from '@/utils/format';
import { calculatePositionSize } from '@/utils/risk';

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

/** Informational calculator. It is NOT a risk control — limits are enforced by the backend. */
export function PositionSizeCalculator() {
  const {
    register,
    control,
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
