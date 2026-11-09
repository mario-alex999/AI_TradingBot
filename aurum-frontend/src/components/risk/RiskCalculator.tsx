import { useState } from 'react';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { z } from 'zod';
import { Calculator, Scale } from 'lucide-react';
import { Card, CardBody, CardHeader } from '@/components/common/Card';
import { Button } from '@/components/common/Button';
import { Badge } from '@/components/common/Badge';
import { SegmentedControl } from '@/components/common/SegmentedControl';
import { RuleList } from './RuleList';
import { useAssessTrade, usePositionSize, useRiskAdminGate, useRiskConfig } from '@/hooks/useRisk';
import { DEFAULT_INSTRUMENTS } from '@/config/instruments';
import type { Assessment, Calculation, PositionSizeResult, TradeProposalInput } from '@/types/risk';
import { describeError } from '@/utils/errors';
import { formatCurrency, formatDateTime, formatNumber, formatPercent } from '@/utils/format';
import { DECISION_LABEL, DECISION_MEANING, DECISION_TONE } from '@/utils/riskLabels';
import { cn } from '@/utils/cn';

const optionalNumber = z.preprocess((v) => (v === '' || v == null ? undefined : v), z.coerce.number().positive('Must be positive').optional());

const schema = z.object({
  symbol: z.string().min(1),
  direction: z.enum(['BUY', 'SELL']),
  entry: z.coerce.number().positive('Must be positive'),
  stopLoss: z.coerce.number().positive('A stop-loss is required'),
  takeProfit: optionalNumber,
  riskPct: optionalNumber,
  equity: optionalNumber,
  accountCurrency: z.string().optional(),
});
type Values = z.infer<typeof schema>;

function toBody(v: Values): TradeProposalInput {
  const body: TradeProposalInput = { symbol: v.symbol, direction: v.direction, entry: v.entry, stopLoss: v.stopLoss };
  if (v.takeProfit) body.takeProfit = v.takeProfit;
  if (v.riskPct) body.riskPct = v.riskPct;
  if (v.equity) {
    body.equity = v.equity;
    if (v.accountCurrency) body.accountCurrency = v.accountCurrency;
  }
  return body;
}

function Row({ k, v, strong }: { k: string; v: string; strong?: boolean }) {
  return (
    <div className="flex justify-between gap-3 border-b border-line-soft py-1.5 text-xs last:border-0">
      <dt className="text-neutral">{k}</dt>
      <dd className={cn('num text-right text-ink', strong && 'font-semibold')}>{v}</dd>
    </div>
  );
}

function CalcTable({ c }: { c: Calculation }) {
  const m = (v: number | null | undefined) => formatCurrency(v, c.accountCurrency);
  return (
    <dl>
      <Row k="Risk budget" v={`${formatPercent(c.riskPctBudget)} = ${m(c.maxRiskAmount)}`} />
      <Row k="Loss per lot at stop" v={m(c.lossPerLotAtStop)} />
      <Row k="Spread, slippage and commission per lot" v={m(c.costPerLot)} />
      <Row k="Total loss per lot" v={m(c.totalLossPerLot)} />
      <Row k="Raw size (before broker step)" v={`${formatNumber(c.rawVolume, 4)} lots`} />
      <Row k="Estimated loss at stop" v={`${m(c.estimatedLoss)} (${formatPercent(c.estimatedLossPct)})`} strong />
      <Row k="Estimated reward" v={m(c.estimatedReward)} />
      <Row k="Reward-to-risk after costs" v={c.adjustedRiskReward == null ? '—' : `${formatNumber(c.adjustedRiskReward, 2)} (gross ${formatNumber(c.riskReward, 2)})`} />
      <Row k="Margin estimate" v={m(c.marginEstimate)} />
      <Row k="Value method" v={c.valueMethod} />
    </dl>
  );
}

function SizeDetails({ r }: { r: PositionSizeResult }) {
  const c = r.calculation;
  return (
    <div className="flex flex-col gap-4">
      {c?.recommendedVolume == null && c && (
        <p className="text-xs text-down-text">
          Even the broker's minimum lot would risk {formatPercent(c.minVolumeRiskPct)} of equity, more than the budget. The size is never rounded up.
        </p>
      )}
      <RuleList title="Failed checks" rules={r.failedChecks} />
      <RuleList title="Warnings" rules={r.warnings} />
      {c && <CalcTable c={c} />}
      <p className="text-2xs text-neutral">
        {r.note} Equity source: {r.equitySource === 'request' ? 'entered here' : 'account mirror'}. {r.disclaimer}
      </p>
    </div>
  );
}

export function AssessmentResult({ a }: { a: Assessment }) {
  return (
    <div className="flex flex-col gap-3 rounded-md border border-line-soft bg-base-raised p-4" aria-live="polite" aria-label="Assessment result">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={DECISION_TONE[a.decision]} className="text-sm">
          {DECISION_LABEL[a.decision]}
        </Badge>
        <span className="text-xs text-ink-muted">{DECISION_MEANING[a.decision]}</span>
      </div>
      <dl className="grid gap-x-6 sm:grid-cols-2">
        <Row k="Final size" v={a.finalVolume != null ? `${formatNumber(a.finalVolume, 2)} lots` : '—'} strong />
        <Row k="Risk" v={`${formatCurrency(a.monetaryRisk, a.accountCurrency ?? 'USD')} (${formatPercent(a.riskPct)})`} />
        <Row k="Reward-to-risk" v={formatNumber(a.riskReward, 2)} />
        <Row k="Valid until" v={formatDateTime(a.expiresAt)} />
      </dl>
      <RuleList title="Rule violations" rules={a.ruleViolations} />
      <RuleList title="Errors" rules={a.errors} />
      <RuleList title="Warnings" rules={a.warnings} />
      <p className="text-2xs text-neutral">
        Assessment {a.assessmentId.slice(0, 8)} · config v{a.configVersion} ({a.configProfile}) · {a.replayed ? 'replayed result · ' : ''}
        {a.module07.note}
      </p>
    </div>
  );
}

const RISK_PRESETS = [0.25, 0.5, 1, 2];

const chip = (active: boolean) =>
  cn('rounded-full border px-3 py-1 text-xs transition', active ? 'border-gold bg-gold text-white' : 'border-line-control text-ink hover:border-ink/40');

/**
 * Section C: risk engine calculator. Same look as the informational calculator, but every number comes from
 * the backend (broker specification, spread, costs, lot step) and the size is floored, never rounded up.
 */
export function RiskCalculator() {
  const gate = useRiskAdminGate();
  const config = useRiskConfig();
  const size = usePositionSize();
  const assess = useAssessTrade();
  const [assessment, setAssessment] = useState<Assessment | null>(null);
  const {
    register,
    handleSubmit,
    watch,
    setValue,
    formState: { errors },
  } = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: { symbol: 'EURUSD', direction: 'BUY', accountCurrency: 'USD' },
  });
  const symbol = watch('symbol');
  const direction = watch('direction');
  const riskPct = watch('riskPct');
  const configured = config.data?.data?.config.riskPerTradePct;
  const maxRisk = config.data?.data?.config.maxRiskPerTradePct ?? 5;
  const shownRisk = riskPct ? Number(riskPct) : configured;
  const err = size.error ? describeError(size.error) : null;
  const c = size.data?.calculation;

  return (
    <Card>
      <CardHeader
        icon={<Calculator className="h-4 w-4" />}
        title="Risk engine calculator"
        subtitle="Sized by the backend from the broker's contract specification, spread and costs. Floored to the lot step, never rounded up."
      />
      <CardBody className="grid gap-4 md:grid-cols-[minmax(0,1fr)_220px]">
        <form
          noValidate
          className="grid content-start gap-3 sm:grid-cols-2"
          onSubmit={handleSubmit((v) => {
            setAssessment(null);
            size.mutate(toBody(v));
          })}
        >
          <div className="flex flex-col gap-2 sm:col-span-2">
            <div className="flex items-center justify-between">
              <label htmlFor="rc-risk-slider" className="text-xs text-ink-muted">
                Risk per trade
              </label>
              <span className="num text-sm font-semibold text-ink">
                {shownRisk != null ? `${shownRisk.toFixed(2)}%` : '—'}
                {!riskPct && <span className="ml-1 text-2xs font-normal text-neutral">configured</span>}
              </span>
            </div>
            <input
              id="rc-risk-slider"
              type="range"
              min={0.1}
              max={maxRisk}
              step={0.05}
              value={shownRisk ?? 1}
              onChange={(e) => setValue('riskPct', Number(e.target.value), { shouldValidate: true })}
              className="w-full accent-gold"
            />
            <div className="flex flex-wrap gap-1.5" role="group" aria-label="Engine risk presets">
              <button type="button" aria-pressed={!riskPct} onClick={() => setValue('riskPct', undefined)} className={chip(!riskPct)}>
                Configured
              </button>
              {RISK_PRESETS.filter((r) => r <= maxRisk).map((r) => (
                <button
                  key={r}
                  type="button"
                  aria-label={`Engine risk ${r}%`}
                  aria-pressed={Number(riskPct) === r}
                  onClick={() => setValue('riskPct', r, { shouldValidate: true })}
                  className={chip(Number(riskPct) === r)}
                >
                  {r}%
                </button>
              ))}
            </div>
            <span className="text-2xs text-neutral">The engine caps risk at the configured maximum of {formatPercent(maxRisk)}.</span>
          </div>
          <div className="flex flex-col gap-2 sm:col-span-2">
            <span className="text-xs text-ink-muted">Instrument (broker specification is read on the backend)</span>
            <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label="Engine instrument">
              {DEFAULT_INSTRUMENTS.map((i) => (
                <button key={i.symbol} type="button" aria-pressed={symbol === i.symbol} onClick={() => setValue('symbol', i.symbol)} className={chip(symbol === i.symbol)}>
                  {i.symbol}
                </button>
              ))}
              <SegmentedControl label="Direction" className="ml-auto" value={direction} options={['BUY', 'SELL'] as const} onChange={(v) => setValue('direction', v)} />
            </div>
          </div>
          {(
            [
              ['entry', 'Entry price'],
              ['stopLoss', 'Stop-loss (required)'],
              ['takeProfit', 'Take-profit'],
              ['equity', 'Equity (blank = account mirror)'],
            ] as const
          ).map(([name, label]) => (
            <div key={name} className="flex flex-col gap-1">
              <label htmlFor={`rc-${name}`} className="text-xs text-ink-muted">
                {label}
              </label>
              <input id={`rc-${name}`} type="number" step="any" inputMode="decimal" className="field num" aria-invalid={!!errors[name]} {...register(name)} />
              {errors[name] && <span className="text-2xs text-down-text">{errors[name]?.message}</span>}
            </div>
          ))}
          <div className="flex flex-col gap-1">
            <label htmlFor="rc-ccy" className="text-xs text-ink-muted">
              Equity currency
            </label>
            <input id="rc-ccy" className="field" maxLength={3} {...register('accountCurrency')} />
          </div>
          <div className="flex flex-wrap items-end gap-2">
            <Button type="submit" loading={size.isPending} icon={<Calculator className="h-3.5 w-3.5" />}>
              Calculate size
            </Button>
            <Button
              type="button"
              variant="secondary"
              icon={<Scale className="h-3.5 w-3.5" />}
              disabled={!gate.allowed}
              disabledReason={gate.reason}
              loading={assess.isPending}
              onClick={handleSubmit((v) => {
                const { equity: _e, accountCurrency: _c, ...rest } = toBody(v);
                assess.mutate(rest, { onSuccess: setAssessment });
              })}
            >
              Run full assessment
            </Button>
          </div>
          <p className="text-2xs text-neutral sm:col-span-2">
            "Calculate size" is a what-if: nothing is stored. "Run full assessment" applies every account, portfolio and market rule to the live account and records the
            decision. Neither places an order.
          </p>
        </form>
        <div className="flex flex-col justify-center gap-3 rounded-md border border-line-soft bg-base-raised p-4" aria-live="polite">
          <div>
            <p className="eyebrow">Amount at risk</p>
            <p className="num mt-1 text-lg font-semibold text-ink">{c ? formatCurrency(c.estimatedLoss, c.accountCurrency) : '—'}</p>
          </div>
          <div>
            <p className="eyebrow">Position size</p>
            <p className="num mt-1 text-2xl text-ink" data-testid="engine-lots">
              {c?.recommendedVolume != null ? `${formatNumber(c.recommendedVolume, 2)} lots` : '—'}
            </p>
          </div>
          {size.data && <Badge tone={size.data.valid ? 'up' : 'down'}>{size.data.valid ? 'Valid trade inputs' : 'Not valid'}</Badge>}
          {assessment && (
            <Badge tone={DECISION_TONE[assessment.decision]} className="self-start">
              Engine decision: {DECISION_LABEL[assessment.decision]}
            </Badge>
          )}
        </div>
        {(assessment || err || size.data) && (
          <div className="flex flex-col gap-4 md:col-span-2">
            {assessment && <AssessmentResult a={assessment} />}
            {err && (
              <p role="alert" className="text-xs text-down-text">
                {err.title}: {err.description}
              </p>
            )}
            {size.data && <SizeDetails r={size.data} />}
          </div>
        )}
      </CardBody>
    </Card>
  );
}
