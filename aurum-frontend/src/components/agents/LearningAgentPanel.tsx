import { useEffect, useState } from 'react';
import { ArrowRight, Copy, GitBranch, RotateCcw, Search, ShieldCheck, Skull, Sprout } from 'lucide-react';
import { Card, CardBody, CardHeader } from '@/components/common/Card';
import { Badge, type Tone } from '@/components/common/Badge';
import { DataSourceBadge } from '@/components/common/DataSourceBadge';
import { ErrorState } from '@/components/common/ErrorState';
import { SkeletonRows } from '@/components/common/Skeleton';
import { useLearningLineage } from '@/hooks/queries';
import { formatDateTime, formatNumber, relativeTime } from '@/utils/format';
import { cn } from '@/utils/cn';
import { toast } from '@/stores/toastStore';
import type { LearningAgent, LearningAgentStatus, LearningLineage } from '@/types/learning';

const STATUS: Record<LearningAgentStatus, { label: string; tone: Tone }> = {
  discovering: { label: 'Looking for what works', tone: 'info' },
  trialling: { label: 'On trial', tone: 'gold' },
  replicated: { label: 'Replicated', tone: 'up' },
  terminated: { label: 'Terminated', tone: 'down' },
  capped: { label: 'Generation limit', tone: 'neutral' },
};

const describe = (p: [string, string][] | null | undefined) => (p && p.length ? p.map(([k, v]) => `${k} = ${v}`).join(' and ') : '—');
const pf = (v: number | null | undefined) => (v === null || v === undefined ? '∞' : v.toFixed(2));

const RUN_CMD = 'python -m aurum.cli learn run --loop';

/** How the cycle works; shown before the backend has a lineage (and always in demo mode). */
function CycleExplainer({ demo }: { demo: boolean }) {
  const steps = [
    { icon: Search, title: 'Monitor', body: 'Reads every closed trade from MT5 history (or a CSV) and tags it: instrument, side, session, weekday, strategy, holding time.' },
    { icon: Sprout, title: 'Identify what works', body: 'In the last 100 trades, finds the pattern with the strongest positive edge: ≥20 trades and profit factor ≥1.3.' },
    { icon: RotateCcw, title: 'Repeat on new trades', body: 'Judges that pattern on its next 10 matching trades, never the ones it was found on.' },
    { icon: GitBranch, title: 'Works → replicate', body: 'Creates one child (next generation) that repeats the cycle on the newest trades.' },
    { icon: Skull, title: '3 failures → terminate', body: 'After three failed trials in a row the agent ends itself and records why.' },
  ];
  return (
    <div className="flex flex-col gap-5">
      <ol className="grid gap-3 md:grid-cols-5">
        {steps.map((s, i) => (
          <li key={s.title} className="relative flex flex-col gap-2 rounded-xl bg-base-raised p-4">
            <span className="flex items-center gap-2 text-sm font-semibold text-ink">
              <s.icon className="h-4 w-4 text-gold" aria-hidden />
              {s.title}
            </span>
            <span className="text-xs leading-relaxed text-ink-muted">{s.body}</span>
            {i < steps.length - 1 && (
              <ArrowRight className="absolute -right-3 top-1/2 z-10 hidden h-4 w-4 -translate-y-1/2 text-neutral md:block" aria-hidden />
            )}
          </li>
        ))}
      </ol>
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-dashed border-line-control px-4 py-3">
        <p className="text-sm text-ink-muted">
          {demo
            ? 'Demo mode never shows agent results: they are trading performance, which is never simulated. Switch to backend data once the agent has run.'
            : 'No lineage yet. Start the agent on the machine that runs the backend:'}
        </p>
        <button
          type="button"
          onClick={() =>
            navigator.clipboard
              ?.writeText(RUN_CMD)
              .then(() => toast({ tone: 'success', title: 'Command copied' }))
              .catch(() => toast({ tone: 'warning', title: 'Copy unavailable', description: RUN_CMD }))
          }
          className="flex items-center gap-2 rounded-full bg-base-raised px-3 py-1.5 font-mono text-xs text-ink transition hover:bg-line-soft"
          aria-label={`Copy command: ${RUN_CMD}`}
        >
          {RUN_CMD}
          <Copy className="h-3.5 w-3.5 text-neutral" aria-hidden />
        </button>
      </div>
    </div>
  );
}

function FailureDots({ failed, max }: { failed: number; max: number }) {
  return (
    <span className="flex items-center gap-1" aria-label={`${failed} of ${max} failed trials in a row`}>
      {Array.from({ length: max }, (_, i) => (
        <span key={i} className={cn('h-2.5 w-2.5 rounded-full', i < failed ? 'bg-down' : 'bg-line-control')} />
      ))}
    </span>
  );
}

function AgentDetail({ a, lin }: { a: LearningAgent; lin: LearningLineage }) {
  const st = STATUS[a.status];
  const progress = Math.min(1, a.trialProgress.matched / lin.config.trialTrades);
  return (
    <div className="grid gap-5 lg:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)]">
      <div className="flex flex-col gap-4">
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="text-base font-semibold text-ink">{a.id}</h3>
          <Badge tone={st.tone}>{st.label}</Badge>
          <span className="text-xs text-neutral">
            Generation {a.generation}
            {a.parent ? ` · created by ${a.parent}` : ' · first generation'}
          </span>
        </div>

        <div className="rounded-xl bg-base-raised p-4">
          <p className="eyebrow">What works (current pattern)</p>
          <p className="mt-1 text-sm font-medium text-ink">{describe(a.pattern ?? a.trials.at(-1)?.pattern)}</p>
          {a.discoveryStats && (
            <p className="num mt-1 text-xs text-ink-muted">
              Found on {a.discoveryStats.trades} trades · net {formatNumber(a.discoveryStats.net, 2)} · profit factor{' '}
              {pf(a.discoveryStats.profitFactor)} · win rate {a.discoveryStats.winRate}%
            </p>
          )}
        </div>

        {a.status === 'trialling' && (
          <div>
            <div className="flex justify-between text-xs">
              <span className="text-ink-muted">Trial {a.trials.length + 1} progress</span>
              <span className="num text-ink">
                {a.trialProgress.matched} / {lin.config.trialTrades} matching trades
              </span>
            </div>
            <div className="mt-1.5 h-2 rounded-full bg-base-raised">
              <div className="h-2 rounded-full bg-gold transition-[width] duration-500" style={{ width: `${progress * 100}%` }} />
            </div>
            <p className="mt-1 text-xs text-neutral">{a.trialProgress.seen} trades closed since the pattern was found.</p>
          </div>
        )}

        <div className="flex items-center justify-between rounded-xl bg-base-raised px-4 py-3">
          <span className="text-sm text-ink-muted">Failed trials in a row</span>
          <FailureDots failed={a.failedTrials} max={lin.config.maxFailedTrials} />
        </div>

        <div>
          <p className="eyebrow mb-2">Trials</p>
          {a.trials.length === 0 ? (
            <p className="text-sm text-neutral">No trial has finished yet.</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[460px] text-sm">
                <caption className="sr-only">Trials of {a.id}</caption>
                <thead>
                  <tr className="text-xs text-neutral">
                    <th scope="col" className="py-1.5 text-left font-normal">#</th>
                    <th scope="col" className="py-1.5 text-left font-normal">Pattern</th>
                    <th scope="col" className="py-1.5 text-right font-normal">Trades</th>
                    <th scope="col" className="py-1.5 text-right font-normal">Net</th>
                    <th scope="col" className="py-1.5 text-right font-normal">PF</th>
                    <th scope="col" className="py-1.5 text-right font-normal">Result</th>
                  </tr>
                </thead>
                <tbody>
                  {a.trials.map((t) => (
                    <tr key={t.number} className="border-t border-line-soft">
                      <td className="num py-2 text-neutral">{t.number}</td>
                      <td className="py-2 text-ink">{describe(t.pattern)}</td>
                      <td className="num py-2 text-right">{t.stats.trades}</td>
                      <td className={cn('num py-2 text-right', t.stats.net >= 0 ? 'text-up' : 'text-down-text')}>{formatNumber(t.stats.net, 2)}</td>
                      <td className="num py-2 text-right">{pf(t.stats.profitFactor)}</td>
                      <td className="py-2 text-right">
                        <Badge tone={t.passed ? 'up' : 'down'}>{t.passed ? 'Passed' : t.inconclusive ? 'Inconclusive' : 'Failed'}</Badge>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>

      <div>
        <p className="eyebrow mb-2">Activity</p>
        <ol className="flex max-h-[360px] flex-col gap-0 overflow-y-auto">
          {[...a.events].reverse().map((e, i) => (
            <li key={`${e.at}-${i}`} className="relative border-l border-line pb-3 pl-4">
              <span
                className={cn(
                  'absolute -left-[5px] top-1 h-2.5 w-2.5 rounded-full',
                  e.kind === 'terminated' || e.kind === 'trial_failed'
                    ? 'bg-down'
                    : e.kind === 'trial_passed' || e.kind === 'replicated'
                      ? 'bg-up'
                      : 'bg-line-control',
                )}
                aria-hidden
              />
              <p className="text-sm text-ink">{e.message}</p>
              <p className="num text-xs text-neutral">{formatDateTime(e.at)} UTC</p>
            </li>
          ))}
        </ol>
      </div>
    </div>
  );
}

/** Trade Learning Agent: lineage, current trial and history, from GET /agents/learning. */
export function LearningAgentPanel() {
  const q = useLearningLineage();
  const lin = q.data?.data ?? null;
  const [selected, setSelected] = useState<string | null>(null);
  useEffect(() => {
    if (lin && (!selected || !lin.agents.some((a) => a.id === selected))) setSelected(lin.activeAgent ?? lin.agents.at(-1)?.id ?? null);
  }, [lin, selected]);
  const agent = lin?.agents.find((a) => a.id === selected) ?? null;

  return (
    <Card>
      <CardHeader
        title="Trade Learning Agent"
        subtitle="Watches every closed trade, learns what works, re-tests it on new trades, replicates when it holds, and terminates itself after 3 failed trials."
        actions={
          <>
            <DataSourceBadge source={q.data?.source} fetchedAt={q.data?.fetchedAt} />
            <span className="flex items-center gap-1 text-xs text-neutral">
              <ShieldCheck className="h-3.5 w-3.5" aria-hidden /> Read-only · never trades
            </span>
          </>
        }
      />
      <CardBody>
        {q.isPending ? (
          <SkeletonRows rows={5} />
        ) : q.isError ? (
          <ErrorState error={q.error} onRetry={() => void q.refetch()} compact />
        ) : !lin ? (
          <CycleExplainer demo={q.data?.source === 'demo'} />
        ) : (
          <div className="flex flex-col gap-5">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <p className="text-sm text-ink-muted">
                Lineage {lin.id} · <span className="num text-ink">{lin.tradesObserved.toLocaleString()}</span> trades observed
                {lin.lastCloseTime && <> · last trade closed {relativeTime(lin.lastCloseTime)}</>}
              </p>
              <Badge tone={lin.status === 'running' ? 'gold' : lin.status === 'terminated' ? 'down' : 'neutral'}>
                {lin.status === 'running' ? 'Running' : STATUS[lin.status as LearningAgentStatus]?.label ?? lin.status}
              </Badge>
            </div>
            <nav aria-label="Generations" className="flex flex-wrap items-center gap-2">
              {lin.agents.map((a, i) => (
                <span key={a.id} className="flex items-center gap-2">
                  {i > 0 && <ArrowRight className="h-3.5 w-3.5 text-neutral" aria-hidden />}
                  <button
                    type="button"
                    aria-pressed={a.id === selected}
                    onClick={() => setSelected(a.id)}
                    className={cn(
                      'flex items-center gap-2 rounded-full border px-3 py-1.5 text-xs font-medium transition',
                      a.id === selected ? 'border-gold bg-gold text-white' : 'border-line-control text-ink hover:border-ink/40',
                    )}
                  >
                    <span
                      className={cn(
                        'h-2 w-2 rounded-full',
                        a.status === 'replicated' ? 'bg-up' : a.status === 'terminated' ? 'bg-down' : a.status === 'capped' ? 'bg-neutral' : 'animate-pulse bg-amber-400',
                      )}
                      aria-hidden
                    />
                    G{a.generation}
                    <span className="sr-only">: {STATUS[a.status].label}</span>
                  </button>
                </span>
              ))}
            </nav>
            {agent && <AgentDetail a={agent} lin={lin} />}
          </div>
        )}
      </CardBody>
    </Card>
  );
}
