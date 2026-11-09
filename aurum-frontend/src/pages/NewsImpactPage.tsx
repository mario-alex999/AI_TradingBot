import { useMemo, useState, type ReactNode } from 'react';
import { ArrowDownRight, ArrowUpRight, ChevronDown, ExternalLink, Minus, Newspaper, ShieldAlert, Shuffle } from 'lucide-react';
import { PageHeader } from '@/components/common/PageHeader';
import { Card, CardBody, CardHeader } from '@/components/common/Card';
import { Badge, type Tone } from '@/components/common/Badge';
import { Select } from '@/components/common/Select';
import { EmptyState } from '@/components/common/EmptyState';
import { QueryState } from '@/components/common/QueryState';
import { DataSourceBadge } from '@/components/common/DataSourceBadge';
import { useNewsImpact } from '@/hooks/queries';
import { useClock } from '@/hooks/useClock';
import { formatDateTime, formatDuration, formatInZone } from '@/utils/format';
import { humanize } from '@/utils/labels';
import { cn } from '@/utils/cn';
import type { Direction, EventOutcome, ImpactEvent, ImpactHeadline, NewsImpactReport, ReactionHistory, Scenario } from '@/types/newsImpact';

const EFFECT: Record<string, { label: string; tone: Tone }> = {
  bullish: { label: 'bullish', tone: 'up' },
  bearish: { label: 'bearish', tone: 'down' },
  neutral: { label: 'little change', tone: 'neutral' },
};
const SCENARIO_TONE: Record<string, Tone> = { above: 'up', inline: 'neutral', below: 'down' };

function DirectionTag({ d, small }: { d: Direction; small?: boolean }) {
  const cls = cn('inline-flex items-center gap-1 font-medium', small ? 'text-xs' : 'text-sm');
  if (d === 'up') return <span className={cn(cls, 'text-up')}><ArrowUpRight className="h-4 w-4" aria-hidden />Up</span>;
  if (d === 'down') return <span className={cn(cls, 'text-down-text')}><ArrowDownRight className="h-4 w-4" aria-hidden />Down</span>;
  if (d === 'mixed') return <span className={cn(cls, 'text-warn')}><Shuffle className="h-3.5 w-3.5" aria-hidden />Mixed</span>;
  return <span className={cn(cls, 'text-neutral')}><Minus className="h-3.5 w-3.5" aria-hidden />No clear move</span>;
}

function pct(v: number | null | undefined, digits = 2): string {
  return v == null ? '—' : `${v > 0 ? '+' : ''}${v.toFixed(digits)}%`;
}

function History({ h }: { h: ReactionHistory | null }) {
  if (!h) return <span className="text-xs text-neutral">No stored history</span>;
  return (
    <span className="text-xs text-ink-muted">
      <span className="num text-ink">{pct(h.avgMovePct)}</span> avg in {h.minutes}m · {h.samples} release{h.samples === 1 ? '' : 's'}
      {h.followedRulePct != null && (
        <>
          {' · '}
          <span className={cn('num', h.followedRulePct >= 60 ? 'text-up' : h.followedRulePct <= 40 ? 'text-down-text' : 'text-ink')}>{h.followedRulePct}%</span> as expected
        </>
      )}
    </span>
  );
}

function value(v: ImpactEvent['actual'], unit: string | null): string {
  if (v == null) return '—';
  return typeof v === 'number' ? `${Number(v.toFixed(4))}${unit === '%' ? '%' : ''}` : String(v);
}

function Figures({ e }: { e: ImpactEvent }) {
  const items: [string, string, boolean][] = [
    ['Actual', value(e.actual, e.unit), e.released],
    ['Forecast', value(e.consensus, e.unit), false],
    ['Previous', value(e.previous, e.unit), false],
  ];
  return (
    <dl className="grid grid-cols-3 gap-3">
      {items.map(([k, v, strong]) => (
        <div key={k}>
          <dt className="eyebrow">{k}</dt>
          <dd className={cn('num mt-0.5 text-sm', strong ? 'font-semibold text-ink' : 'text-ink-muted')}>{v}</dd>
        </div>
      ))}
    </dl>
  );
}

function OutcomePanel({ e, o }: { e: ImpactEvent; o: EventOutcome }) {
  if (!o.scenario) return <p className="rounded-lg bg-base-raised px-3 py-2 text-xs text-ink-muted">{o.note}</p>;
  const eff = EFFECT[o.currencyEffect ?? 'neutral']!;
  return (
    <div className="flex flex-col gap-2 rounded-lg border border-gold/30 bg-gold-faint/60 p-3" data-testid="outcome">
      <p className="text-sm text-ink">
        <span className="font-semibold">Outcome: </span>
        {o.scenario === 'inline' ? 'in line with the forecast' : `${o.scenario} the forecast`}
        {o.surprise != null && o.scenario !== 'inline' && (
          <span className="num text-ink-muted">
            {' '}
            (surprise {o.surprise > 0 ? '+' : ''}
            {Number(o.surprise.toFixed(4))}
            {o.surprisePct != null ? `, ${pct(o.surprisePct, 1)} of forecast` : ''})
          </span>
        )}
        {' → '}
        <Badge tone={eff.tone}>
          {e.currency} {eff.label}
        </Badge>
      </p>
      <ul className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
        {o.instruments.map((r) => (
          <li key={r.instrument} className="flex items-center justify-between gap-3 rounded-lg bg-base-card px-3 py-2">
            <span className="num text-sm font-medium text-ink">{r.instrument}</span>
            <span className="flex flex-col items-end gap-0.5">
              <DirectionTag d={r.expected} small />
              <span className="text-2xs text-ink-muted">
                {r.movePct == null ? 'No candles yet' : `Moved ${pct(r.movePct)} in ${r.minutesMeasured}m`}
                {r.followedRule != null && (r.followedRule ? ' · as expected' : ' · against the rule')}
              </span>
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function ScenarioTable({ e }: { e: ImpactEvent }) {
  const instruments = e.instruments.map((i) => i.instrument);
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[560px] text-left text-sm">
        <caption className="sr-only">Expected reaction by scenario for {e.name}</caption>
        <thead>
          <tr className="border-b border-line text-xs text-neutral">
            <th className="py-2 pr-3 font-normal">If the figure is…</th>
            <th className="py-2 pr-3 font-normal">{e.currency}</th>
            {instruments.map((s) => (
              <th key={s} className="num py-2 pr-3 font-normal">
                {s}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {e.scenarios.map((sc: Scenario) => {
            const eff = EFFECT[sc.currencyEffect ?? 'neutral']!;
            const happened = e.outcome?.scenario === sc.key;
            return (
              <tr key={sc.key} className={cn('border-b border-line-soft align-top last:border-0', happened && 'bg-gold-faint/50')}>
                <td className="py-2.5 pr-3">
                  <Badge tone={SCENARIO_TONE[sc.key]}>{sc.label}</Badge>
                  {happened && <span className="ml-1.5 text-2xs font-semibold text-gold">Happened</span>}
                </td>
                <td className="py-2.5 pr-3">
                  <Badge tone={eff.tone}>{eff.label}</Badge>
                </td>
                {sc.instruments.map((r) => (
                  <td key={r.instrument} className="py-2.5 pr-3" title={r.explanation}>
                    <div className="flex flex-col gap-0.5">
                      <DirectionTag d={r.direction} small />
                      {sc.key !== 'inline' && <History h={r.history} />}
                    </div>
                  </td>
                ))}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function EventCard({ e, now, defaultOpen }: { e: ImpactEvent; now: number; defaultOpen: boolean }) {
  const [open, setOpen] = useState(defaultOpen);
  const at = Date.parse(e.scheduledAt);
  const when = e.released ? `Released ${formatDuration(now - at)} ago` : at > now ? `In ${formatDuration(at - now)}` : 'Awaiting the figure';
  return (
    <Card className="overflow-hidden" data-testid="impact-event">
      <button type="button" className="flex w-full flex-wrap items-start justify-between gap-3 p-4 text-left hover:bg-base-raised/60" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
        <div className="flex min-w-0 items-start gap-3">
          <span className="num mt-0.5 flex h-9 w-12 flex-none items-center justify-center rounded-lg bg-base-raised text-xs font-semibold text-ink">{e.currency ?? '—'}</span>
          <div className="min-w-0">
            <p className="text-sm font-semibold text-ink">{e.name}</p>
            <p className="mt-0.5 text-xs text-ink-muted">
              {formatDateTime(e.scheduledAt)} UTC · <span className={cn(!e.released && at > now && 'text-ink')}>{when}</span>
            </p>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone={e.importance === 'high' ? 'down' : 'warn'}>{humanize(e.importance ?? 'unknown')} impact</Badge>
          {e.outcome?.scenario && <Badge tone={SCENARIO_TONE[e.outcome.scenario]}>{e.outcome.scenario === 'inline' ? 'In line' : `${humanize(e.outcome.scenario)} forecast`}</Badge>}
          <ChevronDown className={cn('h-4 w-4 text-neutral transition-transform', open && 'rotate-180')} aria-hidden />
        </div>
      </button>
      {open && (
        <div className="flex flex-col gap-4 border-t border-line-soft p-4">
          <div className="grid gap-4 lg:grid-cols-[minmax(0,320px)_1fr]">
            <div className="flex flex-col gap-3">
              <Figures e={e} />
              {e.expectation && <p className="text-xs text-ink-muted">{e.expectation.text}</p>}
              <p className="text-xs text-ink-muted">{e.rule}</p>
              {e.sourceUrl && (
                <a className="inline-flex items-center gap-1 text-xs text-ink underline decoration-line-control underline-offset-4" href={e.sourceUrl} target="_blank" rel="noopener noreferrer">
                  Source <ExternalLink className="h-3 w-3" aria-hidden />
                </a>
              )}
            </div>
            <div className="flex flex-col gap-3">
              {e.outcome && <OutcomePanel e={e} o={e.outcome} />}
              {e.scenarios.length > 0 ? (
                <ScenarioTable e={e} />
              ) : (
                <p className="rounded-lg bg-base-raised px-3 py-2 text-xs text-ink-muted">
                  Affects {e.instruments.map((i) => i.instrument).join(', ') || 'no configured instrument'}. No fixed numeric rule: watch the headline tone below.
                </p>
              )}
            </div>
          </div>
        </div>
      )}
    </Card>
  );
}

function NextEvent({ e, now }: { e: ImpactEvent; now: number }) {
  const at = Date.parse(e.scheduledAt);
  const above = e.scenarios.find((s) => s.key === 'above');
  return (
    <section className="flex flex-col gap-4 overflow-hidden rounded-card bg-feature p-5 text-white lg:flex-row lg:items-center lg:justify-between" aria-labelledby="next-title">
      <div className="min-w-0">
        <p className="text-xs text-feature-muted">Next high-impact release</p>
        <h2 id="next-title" className="mt-1 text-lg font-semibold">
          {e.currency} · {e.name}
        </h2>
        <p className="mt-1 text-sm text-feature-muted">
          {formatInZone(e.scheduledAt, 'UTC', { weekday: 'short', day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' })} UTC · forecast{' '}
          {value(e.consensus, e.unit)} vs previous {value(e.previous, e.unit)}
        </p>
      </div>
      <div className="flex flex-wrap items-center gap-6">
        {above && (
          <div className="text-sm">
            <p className="text-xs text-feature-muted">If above forecast</p>
            <p className="mt-1 flex flex-wrap gap-3">
              {above.instruments
                .filter((r) => r.direction === 'up' || r.direction === 'down')
                .map((r) => (
                  <span key={r.instrument} className="num inline-flex items-center gap-1">
                    {r.instrument} {r.direction === 'up' ? <ArrowUpRight className="h-4 w-4 text-feature-tag" aria-label="up" /> : <ArrowDownRight className="h-4 w-4 text-[#FFB4AE]" aria-label="down" />}
                  </span>
                ))}
            </p>
          </div>
        )}
        <div className="text-right">
          <p className="text-xs text-feature-muted">Starts in</p>
          <p className="num text-2xl font-semibold">{formatDuration(at - now)}</p>
        </div>
      </div>
    </section>
  );
}

function Headlines({ items }: { items: ImpactHeadline[] }) {
  if (items.length === 0) return <EmptyState compact title="No high-impact headlines" description="Central-bank announcements and strongly worded market headlines appear here." />;
  return (
    <ul className="flex flex-col divide-y divide-line-soft">
      {items.map((h) => (
        <li key={h.id} className="flex flex-col gap-1.5 py-3 first:pt-0 last:pb-0">
          <a href={h.url} target="_blank" rel="noopener noreferrer" className="text-sm text-ink hover:underline">
            {h.headline}
          </a>
          <div className="flex flex-wrap items-center gap-1.5 text-xs text-ink-muted">
            <span>{h.source}</span>
            <span>· {formatDateTime(h.publishedAt)} UTC</span>
            <Badge tone={h.tone.label === 'hawkish' || h.tone.label === 'positive' ? 'up' : h.tone.label === 'dovish' || h.tone.label === 'negative' ? 'down' : 'neutral'}>
              {humanize(h.tone.label)}
            </Badge>
            {h.effect && (
              <Badge tone={h.effect.bias === 'bullish' ? 'up' : 'down'}>
                {h.effect.currencies.join('/')} {h.effect.bias}
              </Badge>
            )}
          </div>
          {h.instruments.length > 0 && (
            <p className="flex flex-wrap gap-3 text-xs">
              {h.instruments.map((r) => (
                <span key={r.instrument} className="num inline-flex items-center gap-1 text-ink" title={r.explanation}>
                  {r.instrument} <DirectionTag d={r.direction} small />
                </span>
              ))}
            </p>
          )}
          {h.tone.terms.length > 0 && <p className="text-2xs text-neutral">Matched: {h.tone.terms.join(', ')}</p>}
        </li>
      ))}
    </ul>
  );
}

function Body({ r, now }: { r: NewsImpactReport; now: number }) {
  const released = r.events.filter((e) => e.released || Date.parse(e.scheduledAt) <= now);
  const upcoming = r.events.filter((e) => !released.includes(e));
  const section = (title: string, list: ImpactEvent[], open: (i: number) => boolean, empty: string): ReactNode => (
    <section className="flex flex-col gap-3" aria-label={title}>
      <h2 className="text-base font-semibold text-ink">
        {title} <span className="num text-sm font-normal text-neutral">{list.length}</span>
      </h2>
      {list.length === 0 ? <p className="text-xs text-ink-muted">{empty}</p> : list.map((e, i) => <EventCard key={e.id} e={e} now={now} defaultOpen={open(i)} />)}
    </section>
  );
  return (
    <div className="grid gap-5 2xl:grid-cols-[1fr_400px]">
      <div className="flex flex-col gap-5">
        {r.nextEvent && <NextEvent e={r.nextEvent} now={now} />}
        {section('Released', [...released].reverse(), (i) => i === 0, 'No high-impact release in the last 24 hours.')}
        {section('Coming up', upcoming, (i) => i === 0, 'No high-impact event in the selected window.')}
      </div>
      <Card className="h-fit">
        <CardHeader title="High-impact headlines" subtitle="Central-bank and strongly worded market headlines, with their rule-based tone" icon={<Newspaper className="h-4 w-4" />} />
        <CardBody>
          <Headlines items={r.headlines} />
        </CardBody>
      </Card>
    </div>
  );
}

/** High-impact news: what is coming, what the market expects, what each outcome usually means, and what happened. */
export default function NewsImpactPage() {
  const now = useClock();
  const [importance, setImportance] = useState('high');
  const [hoursAhead, setHoursAhead] = useState('168');
  const [currency, setCurrency] = useState('');
  const q = useNewsImpact({ importance, hoursAhead: Number(hoursAhead), currency: currency || undefined });
  const currencies = useMemo(() => [...new Set((q.data?.data?.events ?? []).map((e) => e.currency).filter(Boolean) as string[])].sort(), [q.data]);
  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        eyebrow="Intelligence"
        title="News Impact"
        description="High-impact economic releases and headlines, what the market expects, how each outcome usually moves your instruments, and what actually happened."
        actions={<DataSourceBadge source={q.data?.source} fetchedAt={q.data?.fetchedAt} />}
      />
      <div className="flex gap-2 rounded-card border border-warn/30 bg-warn-faint px-4 py-3 text-xs text-ink-muted">
        <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0 text-warn" aria-hidden />
        <p>
          {q.data?.data?.disclaimer ??
            'Conditional scenarios and measured history, not a forecast: the actual figure is unknown until release, and markets can react against the conventional rule.'}
        </p>
      </div>
      <Card className="grid gap-3 p-3 sm:grid-cols-3">
        <Select label="Impact" value={importance} onChange={setImportance} options={[{ value: 'high', label: 'High only' }, { value: 'high,medium', label: 'High and medium' }]} />
        <Select label="Window" value={hoursAhead} onChange={setHoursAhead} options={[{ value: '24', label: 'Next 24 hours' }, { value: '72', label: 'Next 3 days' }, { value: '168', label: 'Next 7 days' }]} />
        <Select label="Currency" value={currency} onChange={setCurrency} options={[{ value: '', label: 'All' }, ...currencies.map((c) => ({ value: c, label: c }))]} />
      </Card>
      <QueryState
        query={q}
        isEmpty={(r) => !r.data}
        empty={
          <Card>
            <EmptyState
              icon={<Newspaper className="h-5 w-5" aria-hidden />}
              title="No news data"
              description="News and the economic calendar come from the backend (Module 04). AURUM never shows invented events or figures: connect the backend and refresh the calendar and news feeds."
            />
          </Card>
        }
      >
        {(res) => (res.data ? <Body r={res.data} now={now} /> : null)}
      </QueryState>
    </div>
  );
}
