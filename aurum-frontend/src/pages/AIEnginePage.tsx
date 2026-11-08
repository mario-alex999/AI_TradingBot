import { useState } from 'react';
import { Link } from 'react-router-dom';
import { BrainCircuit, Play, ScanSearch, ShieldAlert } from 'lucide-react';
import { EvaluationDetails } from '@/components/ai/EvaluationDetails';
import { Badge, type Tone } from '@/components/common/Badge';
import { Button } from '@/components/common/Button';
import { Card, CardBody, CardHeader } from '@/components/common/Card';
import { DataSourceBadge } from '@/components/common/DataSourceBadge';
import { DataTable, type Column } from '@/components/common/DataTable';
import { EmptyState } from '@/components/common/EmptyState';
import { MetricCard } from '@/components/common/MetricCard';
import { PageHeader } from '@/components/common/PageHeader';
import { QueryState } from '@/components/common/QueryState';
import { Select } from '@/components/common/Select';
import { Tabs } from '@/components/common/Tabs';
import { SignalDrawer } from '@/components/signals/SignalDrawer';
import { DEFAULT_INSTRUMENTS } from '@/config/instruments';
import { SIGNAL_STATUS } from '@/config/signals';
import { useAdminGate, useAiHealth, useEvaluation, useEvaluations, useLatestAnalysis, useProviders, useRunAnalysis, useSetups, useStrategies, useTradeIdeas } from '@/hooks/useDecision';
import { demoProfile } from '@/mocks/market';
import { formatDateTime, formatPercent } from '@/utils/format';
import { humanize } from '@/utils/labels';
import type { AnalysisDetail, DecisionLabel, EvaluationRun, ProviderRow, SetupRow, StrategyRow, TimeframeBias, TradeIdea } from '@/types/decision';

const TABS = [
  { id: 'dashboard', label: 'AI Dashboard' },
  { id: 'timeframes', label: 'Multi-Timeframe Analysis' },
  { id: 'scanner', label: 'Strategy Scanner' },
  { id: 'explanation', label: 'AI Explanation' },
  { id: 'ideas', label: 'Trade Idea Review' },
  { id: 'providers', label: 'AI Provider Settings' },
  { id: 'evaluation', label: 'Historical Evaluation' },
] as const;
type Tab = (typeof TABS)[number]['id'];

const DECISION_TONE: Record<DecisionLabel, Tone> = {
  STRONG_BUY_SETUP: 'up',
  BUY_SETUP: 'up',
  STRONG_SELL_SETUP: 'down',
  SELL_SETUP: 'down',
  WAIT: 'warn',
  NO_TRADE: 'neutral',
  INSUFFICIENT_DATA: 'neutral',
};

const NO_ANALYSIS = <EmptyState icon={<BrainCircuit className="h-5 w-5" aria-hidden />} title="No analysis yet" description="Run an analysis on the dashboard. AURUM AI never fills this page with sample results." />;

function Bullets({ title, items }: { title: string; items: string[] }) {
  return (
    <Card>
      <CardHeader title={title} />
      <CardBody>
        {items.length === 0 ? (
          <p className="text-xs text-neutral">None recorded.</p>
        ) : (
          <ul className="list-inside list-disc space-y-1 text-xs text-ink-muted">
            {items.map((i) => (
              <li key={i}>{i}</li>
            ))}
          </ul>
        )}
      </CardBody>
    </Card>
  );
}

function Dashboard({ a }: { a: AnalysisDetail }) {
  const s = a.summary;
  const best = s?.evidence?.[0];
  const gaps = Object.values(a.availability ?? {}).filter((v) => v !== 'available').length;
  return (
    <div className="flex flex-col gap-4">
      <section aria-label="Latest analysis" className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-5">
        <MetricCard label="Final decision" value={a.decision ? <Badge tone={DECISION_TONE[a.decision]}>{humanize(a.decision)}</Badge> : '—'} hint="Pending human review" />
        <MetricCard label="Market regime" value={humanize(a.regime ?? 'unknown')} hint={s?.regime?.timeframe} />
        <MetricCard label="Overall bias" value={humanize(a.bias ?? 'unknown')} hint={s?.bias ? `${Math.round(s.bias.agreement * 100)}% weighted agreement` : undefined} />
        <MetricCard label="Evidence score" value={best ? `${Math.round(best.finalScore)} · ${best.grade}` : '—'} hint="Agreement of evidence, not a probability" />
        <MetricCard label="Data quality" value={gaps === 0 ? 'Complete' : `${gaps} source${gaps === 1 ? '' : 's'} missing`} tone={gaps === 0 ? 'flat' : 'muted'} hint={`Analysed ${formatDateTime(a.asOf)} UTC`} />
      </section>
      <div className="grid gap-4 xl:grid-cols-2">
        <Bullets title="Why this decision" items={s?.decision?.reasons ?? []} />
        <Bullets title="Conflicts" items={(s?.conflicts ?? []).map((c) => c.detail)} />
      </div>
    </div>
  );
}

interface TimeframeRow extends TimeframeBias {
  tf: string;
}

const TF_COLUMNS: Column<TimeframeRow>[] = [
  { key: 'tf', header: 'Timeframe', cell: (r) => r.tf },
  { key: 'bias', header: 'Bias', cell: (r) => humanize(r.bias) },
  { key: 'state', header: 'Structure', cell: (r) => (r.state ? humanize(r.state) : '—') },
  { key: 'weight', header: 'Weight', cell: (r) => r.weight, align: 'right' },
  { key: 'reason', header: 'Note', cell: (r) => r.reason ?? '—' },
];

function Timeframes({ a }: { a: AnalysisDetail }) {
  const b = a.summary?.bias;
  if (!b) return <Card>{NO_ANALYSIS}</Card>;
  return (
    <Card>
      <CardHeader title="Multi-timeframe bias" subtitle={`Overall ${humanize(b.overall)} at ${Math.round(b.agreement * 100)}% weighted agreement`} />
      <CardBody className="flex flex-col gap-3">
        <DataTable columns={TF_COLUMNS} rows={Object.entries(b.timeframes).map(([tf, v]) => ({ tf, ...v }))} rowKey={(r) => r.tf} caption="Bias by timeframe" dense />
        <p className="text-xs text-neutral">{b.note}</p>
      </CardBody>
    </Card>
  );
}

const SETUP_COLUMNS: Column<SetupRow>[] = [
  { key: 'strategy', header: 'Strategy', cell: (r) => humanize(r.strategy) },
  { key: 'dir', header: 'Direction', cell: (r) => humanize(r.direction) },
  { key: 'tf', header: 'Timeframe', cell: (r) => r.timeframe },
  { key: 'score', header: 'Score', cell: (r) => `${Math.round(r.score)} · ${r.evidenceGrade}`, sortValue: (r) => r.score, align: 'right' },
  { key: 'state', header: 'Status', cell: (r) => (r.valid ? <Badge tone="up">Valid</Badge> : <Badge tone="down">{humanize(r.blockedBy ?? 'blocked')}</Badge>) },
  { key: 'inv', header: 'Invalidation', cell: (r) => r.invalidation },
];

const STRATEGY_COLUMNS: Column<StrategyRow>[] = [
  { key: 'id', header: 'Strategy', cell: (r) => humanize(r.id) },
  { key: 'enabled', header: 'State', cell: (r) => <Badge tone={r.enabled ? 'up' : 'neutral'}>{r.enabled ? 'Enabled' : 'Disabled'}</Badge> },
  { key: 'tf', header: 'Timeframes', cell: (r) => r.timeframes.join(', ') },
  { key: 'align', header: 'Needs alignment', cell: (r) => (r.requireAlignment ? 'Yes' : 'No') },
];

function Scanner({ symbol }: { symbol: string }) {
  const [asked, setAsked] = useState(false);
  const q = useSetups(symbol, asked);
  const strategies = useStrategies();
  return (
    <div className="flex flex-col gap-4">
      <Card>
        <CardHeader
          icon={<ScanSearch className="h-4 w-4" />}
          title={`${symbol} strategy scan`}
          subtitle="Reads the latest stored data and saves nothing."
          actions={
            <Button size="sm" onClick={() => (asked ? void q.refetch() : setAsked(true))} loading={q.isFetching}>
              Scan
            </Button>
          }
        />
        <CardBody>
          {!asked ? (
            <p className="text-xs text-neutral">Not scanned yet.</p>
          ) : (
            <QueryState query={q}>
              {(r) => (
                <div className="flex flex-col gap-3">
                  <DataTable columns={SETUP_COLUMNS} rows={r.data?.setups ?? []} rowKey={(s) => `${s.strategy}-${s.timeframe}-${s.direction}`} caption="Setups found" dense empty={<EmptyState title="No setup found" compact />} />
                  <details className="text-xs text-ink-muted">
                    <summary className="cursor-pointer text-neutral">{r.data?.skipped.length ?? 0} strategy checks found nothing</summary>
                    <ul className="mt-2 list-inside list-disc space-y-0.5">
                      {r.data?.skipped.map((s) => (
                        <li key={`${s.template}-${s.timeframe}`}>
                          {humanize(s.template)} {s.timeframe}: {s.reason}
                        </li>
                      ))}
                    </ul>
                  </details>
                </div>
              )}
            </QueryState>
          )}
        </CardBody>
      </Card>
      <Card>
        <CardHeader title="Registered strategies" />
        <CardBody>
          <QueryState query={strategies} isEmpty={(r) => r.data.length === 0} empty={<EmptyState title="No strategies registered" compact />}>
            {(r) => <DataTable columns={STRATEGY_COLUMNS} rows={r.data} rowKey={(s) => s.id} caption="Registered strategies" dense />}
          </QueryState>
        </CardBody>
      </Card>
    </div>
  );
}

function Explanation({ a }: { a: AnalysisDetail }) {
  const e = a.summary?.explanation;
  if (!e) return <Card>{NO_ANALYSIS}</Card>;
  return (
    <div className="flex flex-col gap-4">
      <Card className="p-4 text-sm text-ink">
        <p>{e.market}</p>
        <p className="mt-2">{e.decision}</p>
        <p className="mt-2 text-xs text-neutral">Rule-based explanation from the stored evidence. It is not an AI review; an AI review, when there is one, appears on the candidate.</p>
      </Card>
      <div className="grid gap-4 xl:grid-cols-2">
        <Bullets title="Bullish evidence" items={e.bullishEvidence} />
        <Bullets title="Bearish evidence" items={e.bearishEvidence} />
        <Bullets title="Conflicting evidence" items={e.conflicts} />
        <Bullets title="Missing data" items={e.missingData} />
      </div>
    </div>
  );
}

const IDEA_COLUMNS: Column<TradeIdea>[] = [
  { key: 'inst', header: 'Instrument', cell: (r) => r.instrument, sortValue: (r) => r.instrument },
  { key: 'dir', header: 'Direction', cell: (r) => humanize(r.direction) },
  { key: 'strategy', header: 'Strategy', cell: (r) => humanize(r.strategy) },
  { key: 'tf', header: 'Timeframe', cell: (r) => r.timeframe },
  { key: 'grade', header: 'Grade', cell: (r) => r.grade, sortValue: (r) => r.grade },
  { key: 'size', header: 'Estimated size', cell: (r) => (r.estimatedVolume === null ? '—' : `${r.estimatedVolume} lots`), align: 'right' },
  { key: 'status', header: 'Status', cell: (r) => <Badge tone={SIGNAL_STATUS[r.status]?.tone ?? 'neutral'}>{humanize(r.reviewStatus)}</Badge> },
  { key: 'time', header: 'Generated (UTC)', cell: (r) => formatDateTime(r.timestamp), sortValue: (r) => r.timestamp },
];

function Ideas() {
  const q = useTradeIdeas();
  const [open, setOpen] = useState<TradeIdea | null>(null);
  return (
    <Card>
      <CardHeader title="Trade ideas" subtitle="Sizes are estimates. Approval marks an idea as accepted for further processing, and places nothing." />
      <CardBody>
        <QueryState query={q} isEmpty={(r) => r.data.length === 0} empty={<EmptyState title="No trade ideas yet" description="Ideas appear here once an analysis finds a candidate." compact />}>
          {(r) => <DataTable columns={IDEA_COLUMNS} rows={r.data} rowKey={(i) => i.id} onRowClick={setOpen} rowLabel={(i) => `Review ${i.instrument} ${i.direction}`} caption="Trade ideas" initialSort={{ key: 'time', dir: 'desc' }} dense />}
        </QueryState>
      </CardBody>
      <SignalDrawer id={open?.id ?? null} digits={open ? demoProfile(open.instrument).digits : undefined} onClose={() => setOpen(null)} />
    </Card>
  );
}

const PROVIDER_COLUMNS: Column<ProviderRow>[] = [
  { key: 'id', header: 'Provider', cell: (r) => humanize(r.id) },
  { key: 'selected', header: 'Selected', cell: (r) => (r.selected ? <Badge tone="gold">Selected</Badge> : '—') },
  { key: 'model', header: 'Model', cell: (r) => r.model ?? '—' },
  { key: 'configured', header: 'Configured', cell: (r) => <Badge tone={r.configured ? 'up' : 'neutral'}>{r.configured ? 'Yes' : 'No'}</Badge> },
  { key: 'budget', header: 'Monthly budget enforced', cell: (r) => (r.budgetEnforced ? 'Yes' : 'No') },
  { key: 'note', header: 'Note', cell: (r) => r.note ?? '—' },
];

function Providers() {
  const q = useProviders();
  const health = useAiHealth();
  return (
    <QueryState query={q} isEmpty={(r) => !r.data} empty={<Card><EmptyState title="Provider status unavailable" description="Provider status comes from the backend and has no demo version." compact /></Card>}>
      {(r) => {
        const st = r.data?.state;
        return (
          <div className="flex flex-col gap-4">
            <section aria-label="AI reviewer state" className="grid grid-cols-2 gap-3 xl:grid-cols-4">
              <MetricCard label="AI reviewer" value={st?.enabled ? 'Enabled' : 'Off'} hint={st?.blockedReason ? humanize(st.blockedReason) : undefined} />
              <MetricCard label="Monthly budget" value={st ? `$${st.budgetUsedUsd.toFixed(2)} of $${st.budgetUsd.toFixed(2)}` : '—'} hint={st?.budgetEnforced ? 'Hard limit' : 'Not enforced for this provider'} />
              <MetricCard label="Hourly call limit" value={st?.maxCallsPerHour ?? '—'} />
              <MetricCard label="Engine" value={health.data?.data?.engineVersion ?? '—'} hint={health.data?.data?.lastRun ? `Last run ${formatDateTime(health.data.data.lastRun)} UTC` : undefined} />
            </section>
            <Card>
              <CardHeader title="Providers" subtitle="Chosen in AURUM_AI/.env, where API keys stay. Keys are never sent to this page." />
              <CardBody>
                <DataTable columns={PROVIDER_COLUMNS} rows={r.data?.providers ?? []} rowKey={(p) => p.id} caption="AI providers" dense />
              </CardBody>
            </Card>
          </div>
        );
      }}
    </QueryState>
  );
}

const RUN_COLUMNS: Column<EvaluationRun>[] = [
  { key: 'created', header: 'Run (UTC)', cell: (r) => formatDateTime(r.createdAt), sortValue: (r) => r.createdAt },
  { key: 'symbols', header: 'Instruments', cell: (r) => r.symbols.join(', ') },
  { key: 'period', header: 'Period', cell: (r) => `${r.from.slice(0, 10)} – ${r.to.slice(0, 10)}` },
  { key: 'trades', header: 'Trades', cell: (r) => r.metrics.totalTrades, align: 'right' },
  { key: 'win', header: 'Win rate', cell: (r) => formatPercent(r.metrics.winRate), align: 'right' },
  { key: 'exp', header: 'Expectancy', cell: (r) => r.metrics.expectancy.toFixed(2), align: 'right' },
  { key: 'dd', header: 'Max drawdown', cell: (r) => formatPercent(r.metrics.maxDrawdownPct), align: 'right' },
];

function Evaluation() {
  const runs = useEvaluations();
  const [id, setId] = useState<string | null>(null);
  const chosen = id ?? runs.data?.data[0]?.id ?? null;
  const q = useEvaluation(chosen);
  const details = q.data?.data?.details;
  return (
    <div className="flex flex-col gap-4">
      <Card>
        <CardHeader title="Evaluation runs" subtitle="Simulated on stored history. Past results do not predict future results." actions={<Link to="/backtesting" className="text-xs text-ink-muted underline">Run a backtest</Link>} />
        <CardBody>
          <QueryState query={runs} isEmpty={(r) => r.data.length === 0} empty={<EmptyState title="No evaluation has been run" compact />}>
            {(r) => <DataTable columns={RUN_COLUMNS} rows={r.data} rowKey={(x) => x.id} onRowClick={(x) => setId(x.id)} rowLabel={(x) => `Open run from ${x.createdAt}`} caption="Evaluation runs" dense />}
          </QueryState>
        </CardBody>
      </Card>
      {details && <EvaluationDetails d={details} />}
    </div>
  );
}

export default function AIEnginePage() {
  const [tab, setTab] = useState<Tab>('dashboard');
  const [symbol, setSymbol] = useState('EURUSD');
  const q = useLatestAnalysis(symbol);
  const gate = useAdminGate();
  const run = useRunAnalysis();
  const needsAnalysis = (body: (a: AnalysisDetail) => JSX.Element) => (
    <QueryState query={q} isEmpty={(r) => !r.data} empty={<Card>{NO_ANALYSIS}</Card>}>
      {(r) => (r.data ? body(r.data) : null)}
    </QueryState>
  );
  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        eyebrow="Research"
        title="AI Engine"
        description="Regime, bias, strategy evidence and the final decision for each instrument. Results are for human review, and AURUM AI never executes trades."
        actions={<DataSourceBadge source={q.data?.source} fetchedAt={q.data?.fetchedAt} />}
      />
      <div className="flex gap-2 rounded-card border border-warn/30 bg-warn-faint px-4 py-3 text-xs text-ink-muted">
        <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0 text-warn" aria-hidden />
        <p>
          A score reflects how far the evidence agrees. It is <strong className="text-ink">not</strong> a probability of success or a guarantee, and every idea needs human review.
        </p>
      </div>
      <Card className="flex flex-wrap items-end justify-between gap-3 p-3">
        <Select label="Instrument" value={symbol} onChange={setSymbol} options={DEFAULT_INSTRUMENTS.map((i) => ({ value: i.symbol, label: i.symbol }))} />
        <Button variant="primary" icon={<Play className="h-4 w-4" aria-hidden />} loading={run.isPending} disabled={!gate.allowed} disabledReason={gate.reason} onClick={() => run.mutate(symbol)} title="Uses the AI reviewer when it is enabled, within its budget">
          Run analysis
        </Button>
      </Card>
      <Tabs label="AI Engine sections" tabs={[...TABS]} value={tab} onChange={setTab} />
      {tab === 'dashboard' && needsAnalysis((a) => <Dashboard a={a} />)}
      {tab === 'timeframes' && needsAnalysis((a) => <Timeframes a={a} />)}
      {tab === 'scanner' && <Scanner key={symbol} symbol={symbol} />}
      {tab === 'explanation' && needsAnalysis((a) => <Explanation a={a} />)}
      {tab === 'ideas' && <Ideas />}
      {tab === 'providers' && <Providers />}
      {tab === 'evaluation' && <Evaluation />}
    </div>
  );
}
