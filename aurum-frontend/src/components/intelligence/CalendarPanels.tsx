import { useState } from 'react';
import { ExternalLink } from 'lucide-react';
import { Card, CardHeader } from '@/components/common/Card';
import { Badge } from '@/components/common/Badge';
import { DataSourceBadge } from '@/components/common/DataSourceBadge';
import { InfoTip } from '@/components/common/InfoTip';
import { Pagination } from '@/components/common/Pagination';
import { SegmentedControl } from '@/components/common/SegmentedControl';
import { Select } from '@/components/common/Select';
import { useEconomicCalendar, useMarketNews } from '@/hooks/queries';
import { useSettingsStore } from '@/stores/settingsStore';
import { DEFAULT_INSTRUMENTS } from '@/config/instruments';
import type { CalendarCoverage, CalendarQuery, EconomicEvent } from '@/types/intelligence';
import { MiQuery, MiTable, Provenance, Td, Th } from './miCommon';
import { fixed, fmtMinutes, fmtTime, humanize, IMPORTANCE_TONE } from './miFormat';

const RANGES = [
  { value: 'today', label: 'Today' },
  { value: 'tomorrow', label: 'Tomorrow' },
  { value: 'this_week', label: 'This week' },
  { value: 'next_week', label: 'Next week' },
] as const;
type Range = (typeof RANGES)[number]['value'];

const ALL = '';
const TONE: Record<string, 'up' | 'down' | 'neutral' | 'info'> = {
  hawkish: 'info', dovish: 'info', neutral: 'neutral', positive: 'up', negative: 'down',
};
const CURRENCIES = ['USD', 'EUR', 'GBP', 'JPY', 'AUD', 'NZD', 'CAD', 'CHF'];
const CATEGORIES = ['monetary_policy', 'inflation', 'employment', 'growth', 'consumer', 'manufacturing', 'housing', 'trade', 'government_fiscal', 'sentiment', 'commodities'];

export function CoverageNotice({ coverage }: { coverage: CalendarCoverage }) {
  if (coverage.level === 'official') {
    const gaps = Object.entries(coverage.gaps ?? {}).filter(([, v]) => v.length);
    const uncovered = Object.entries(coverage.currencies ?? {}).filter(([, v]) => !v.covered).map(([c]) => c);
    return (
      <div role="status" className="mx-4 mb-3 rounded-lg bg-info-faint px-3 py-2 text-xs text-info">
        <strong className="font-semibold">Calendar coverage: official schedules.</strong> {coverage.reason}
        {uncovered.length > 0 && <> Not covered right now: {uncovered.join(', ')} (event risk there stays UNKNOWN).</>}
        {gaps.length > 0 && (
          <details className="mt-1">
            <summary className="cursor-pointer">Known gaps</summary>
            <ul className="mt-1 list-disc pl-4">
              {gaps.map(([c, v]) => (
                <li key={c}>
                  <span className="font-semibold">{c}:</span> {v.join('; ')}
                </li>
              ))}
            </ul>
          </details>
        )}
      </div>
    );
  }
  if (coverage.complete) return null;
  return (
    <div role="status" className="mx-4 mb-3 rounded-lg bg-warn-faint px-3 py-2 text-xs text-warn">
      <strong className="font-semibold">Calendar coverage: {coverage.level}.</strong> {coverage.reason} Events not listed here may still be
      scheduled; event risk is reported as UNKNOWN rather than NORMAL when nothing is found.
    </div>
  );
}

const value = (v: number | null, unit: string | null) => (v == null ? '—' : `${fixed(v, Math.abs(v) >= 100 ? 1 : 2)}${unit ? ` ${unit}` : ''}`);

function EventRow({ e, tz }: { e: EconomicEvent; tz: string }) {
  return (
    <tr>
      <Td muted>
        <span className="num text-xs">{fmtTime(e.scheduledAt, tz)}</span>
        {e.timePrecision !== 'exact' && (
          <span className="ml-1 text-2xs text-neutral" title="The provider did not give an exact release time">
            ({e.timePrecision})
          </span>
        )}
      </Td>
      <Td>{e.currency ?? '—'}</Td>
      <Td>
        <span className="font-medium">{e.name}</span>
        <span className="block text-2xs text-neutral">{humanize(e.category)}</span>
      </Td>
      <Td>{e.importance ? <Badge tone={IMPORTANCE_TONE[e.importance] ?? 'neutral'}>{e.importance}</Badge> : <Badge>unrated</Badge>}</Td>
      <Td right>{value(e.actual, e.unit)}</Td>
      <Td right muted>{value(e.consensus, e.unit)}</Td>
      <Td right muted>
        {value(e.previous, e.unit)}
        {e.revisedPrevious != null && <span className="block text-2xs text-neutral">rev. {value(e.revisedPrevious, e.unit)}</span>}
      </Td>
      <Td muted>
        <span className="text-xs">{e.affectedInstruments.map((a) => a.instrument).join(', ') || '—'}</span>
      </Td>
      <Td muted>
        <span className="text-xs">
          {e.status !== 'scheduled' ? <Badge tone={e.status === 'cancelled' ? 'down' : 'info'}>{e.status}</Badge> : e.minutesUntilRelease > 0 ? `in ${fmtMinutes(e.minutesUntilRelease)}` : 'released'}
        </span>
      </Td>
      <Td muted>
        <span className="text-2xs">
          {e.sourceUrl ? (
            <a href={e.sourceUrl} target="_blank" rel="noreferrer noopener" className="inline-flex items-center gap-0.5 underline decoration-line-control underline-offset-2 hover:text-ink">
              {e.provider} <ExternalLink className="h-3 w-3" aria-hidden />
            </a>
          ) : (
            e.provider
          )}
        </span>
      </Td>
    </tr>
  );
}

/** Panel B: economic calendar with filters. Shows only events received from a configured provider. */
export function EconomicCalendarPanel() {
  const tz = useSettingsStore((s) => s.displayTimezone);
  const [range, setRange] = useState<Range>('this_week');
  const [currency, setCurrency] = useState(ALL);
  const [importance, setImportance] = useState(ALL);
  const [category, setCategory] = useState(ALL);
  const [instrument, setInstrument] = useState(ALL);
  const [page, setPage] = useState(1);
  const query: CalendarQuery = {
    range,
    currencies: currency ? [currency] : undefined,
    importance: importance ? [importance] : undefined,
    categories: category ? [category] : undefined,
    instrument: instrument || undefined,
    page,
    pageSize: 25,
  };
  const q = useEconomicCalendar(query);
  const reset = (fn: (v: string) => void) => (v: string) => {
    fn(v);
    setPage(1);
  };
  return (
    <Card>
      <CardHeader
        title={
          <span className="flex items-center gap-1.5">
            Economic calendar
            <InfoTip>
              Events come only from configured providers (each row names its provider). Missing forecast or actual values are shown as blank, never
              as zero. Actual values appear only after the provider publishes them.
            </InfoTip>
          </span>
        }
        subtitle="Scheduled releases and central-bank decisions"
        actions={<DataSourceBadge source={q.data?.source} fetchedAt={q.data?.fetchedAt} />}
      />
      <div className="flex flex-wrap items-end gap-3 px-4 pb-3 pt-2">
        <SegmentedControl label="Date range" value={range} options={RANGES as unknown as { value: Range; label: string }[]} onChange={reset((v) => setRange(v as Range))} />
        <Select label="Currency" value={currency} onChange={reset(setCurrency)} options={[{ value: ALL, label: 'All currencies' }, ...CURRENCIES.map((c) => ({ value: c, label: c }))]} className="w-36" />
        <Select
          label="Importance"
          value={importance}
          onChange={reset(setImportance)}
          options={[{ value: ALL, label: 'All' }, { value: 'high', label: 'High' }, { value: 'medium', label: 'Medium' }, { value: 'low', label: 'Low' }]}
          className="w-32"
        />
        <Select label="Category" value={category} onChange={reset(setCategory)} options={[{ value: ALL, label: 'All categories' }, ...CATEGORIES.map((c) => ({ value: c, label: humanize(c) }))]} className="w-44" />
        <Select label="Instrument" value={instrument} onChange={reset(setInstrument)} options={[{ value: ALL, label: 'Any' }, ...DEFAULT_INSTRUMENTS.map((i) => ({ value: i.symbol, label: i.symbol }))]} className="w-32" />
      </div>
      <MiQuery query={q}>
        {(r) => (
          <>
            <CoverageNotice coverage={r.coverage} />
            {r.items.length === 0 ? (
              <p className="px-4 py-6 text-center text-xs text-neutral">
                No stored events match these filters{r.coverage.complete ? '.' : ' — and calendar coverage is incomplete, so this does not mean nothing is scheduled.'}
              </p>
            ) : (
              <MiTable
                caption="Economic events"
                minWidth={1080}
                head={
                  <>
                    <Th>Time</Th>
                    <Th>Ccy</Th>
                    <Th>Event</Th>
                    <Th>Impact</Th>
                    <Th right>Actual</Th>
                    <Th right>Forecast</Th>
                    <Th right>Previous</Th>
                    <Th>Instruments</Th>
                    <Th>Status</Th>
                    <Th>Provider</Th>
                  </>
                }
              >
                {r.items.map((e) => (
                  <EventRow key={e.id} e={e} tz={tz} />
                ))}
              </MiTable>
            )}
            <div className="px-4 py-3">
              <Pagination page={r.page} pageSize={r.pageSize} total={r.total} onPage={setPage} />
            </div>
            <Provenance>Providers in use: {r.coverage.providers.join(', ') || 'none'}. Times in {tz}.</Provenance>
          </>
        )}
      </MiQuery>
    </Card>
  );
}

/** Official central-bank announcements and (if configured) reported news; headlines are shown as published. */
export function NewsPanel({ symbol }: { symbol?: string }) {
  const tz = useSettingsStore((s) => s.displayTimezone);
  const [page, setPage] = useState(1);
  const q = useMarketNews(symbol, page);
  return (
    <Card>
      <CardHeader
        title="Official announcements & news"
        subtitle="Headlines exactly as published by the source; AURUM does not write or summarise them. Tone badges are AURUM's rule-based reading of the wording (hover for the matched terms)"
        actions={<DataSourceBadge source={q.data?.source} fetchedAt={q.data?.fetchedAt} />}
      />
      <MiQuery query={q}>
        {(r) => (
          <>
            {r.items.length === 0 ? (
              <p className="px-4 py-6 text-center text-xs text-neutral">No stored headlines. Refresh with `python -m aurum.cli intelligence news-update`.</p>
            ) : (
              <ul className="divide-y divide-line-soft px-4">
                {r.items.map((n) => (
                  <li key={n.id} className="py-2.5">
                    <div className="flex flex-wrap items-center gap-2 text-2xs text-neutral">
                      <span className="num">{fmtTime(n.publishedAt, tz)}</span>
                      <Badge tone={n.sourceType === 'official_announcement' ? 'gold' : 'neutral'}>{n.sourceType === 'official_announcement' ? 'Official' : 'Reported'}</Badge>
                      <span>{n.source}</span>
                      {n.tone && n.tone.label !== 'unclear' && (
                        <span title={`${n.tone.note} Matched: ${n.tone.terms.join(', ')}`}>
                          <Badge tone={TONE[n.tone.label] ?? 'neutral'}>{n.tone.label}</Badge>
                        </span>
                      )}
                      {n.currencies.map((c) => (
                        <Badge key={c}>{c}</Badge>
                      ))}
                    </div>
                    {n.url ? (
                      <a href={n.url} target="_blank" rel="noreferrer noopener" className="mt-1 block text-sm text-ink hover:underline">
                        {n.headline}
                      </a>
                    ) : (
                      <p className="mt-1 text-sm text-ink">{n.headline}</p>
                    )}
                  </li>
                ))}
              </ul>
            )}
            <div className="px-4 py-3">
              <Pagination page={r.page} pageSize={r.pageSize} total={r.total} onPage={setPage} />
            </div>
            <Provenance>{r.note}</Provenance>
          </>
        )}
      </MiQuery>
    </Card>
  );
}
