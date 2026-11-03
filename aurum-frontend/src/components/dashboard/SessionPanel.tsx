import { useState } from 'react';
import { ChevronRight, Clock3 } from 'lucide-react';
import { cn } from '@/utils/cn';
import { Card, CardBody, CardHeader } from '@/components/common/Card';
import { Badge } from '@/components/common/Badge';
import { Select } from '@/components/common/Select';
import { useClock } from '@/hooks/useClock';
import { useSessions } from '@/hooks/useSessions';
import { useSettingsStore } from '@/stores/settingsStore';
import { DISPLAY_TIMEZONES } from '@/config/sessions';
import { formatDuration, formatInZone } from '@/utils/format';
import { startOfZonedDay, type Interval } from '@/utils/sessions';

const HM: Intl.DateTimeFormatOptions = { hour: '2-digit', minute: '2-digit', hourCycle: 'h23' };

function segments(intervals: Interval[], dayStart: number): { start: number; end: number; left: number; width: number }[] {
  const dayEnd = dayStart + 86_400_000;
  return intervals
    .map((i) => ({ s: Math.max(i.start, dayStart), e: Math.min(i.end, dayEnd) }))
    .filter((x) => x.e > x.s)
    .map((x) => ({ start: x.s, end: x.e, left: ((x.s - dayStart) / 86_400_000) * 100, width: ((x.e - x.s) / 86_400_000) * 100 }));
}

export function SessionPanel({ detailed = false }: { detailed?: boolean }) {
  const [expanded, setExpanded] = useState<string | null>(null);
  const now = useClock();
  const tz = useSettingsStore((s) => s.displayTimezone);
  const setTz = useSettingsStore((s) => s.setDisplayTimezone);
  const { sessions, overlapActive, nextOverlap } = useSessions(now);
  const dayStart = startOfZonedDay(now, tz);
  const nowPct = ((now - dayStart) / 86_400_000) * 100;
  const tzLabel = tz === 'UTC' ? 'UTC' : tz.split('/').pop()?.replace('_', ' ');

  return (
    <Card>
      <CardHeader
        icon={<Clock3 className="h-4 w-4" />}
        title="Market sessions"
        subtitle="Times shown in your display timezone. Session hours follow each market's local DST rules."
        actions={
          <Select
            label="Display timezone"
            hideLabel
            value={tz}
            onChange={setTz}
            options={DISPLAY_TIMEZONES.map((z) => ({ value: z, label: z }))}
            className="w-48"
          />
        }
      />
      <CardBody className="flex flex-col gap-4">
        <ul className="flex flex-col gap-3">
          {sessions.map((s) => {
            const shown = s.current ?? s.next;
            return (
              <li key={s.def.id} className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 gap-y-1.5 sm:grid-cols-[150px_minmax(0,1fr)_auto]">
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    aria-expanded={expanded === s.def.id}
                    aria-controls={`session-${s.def.id}`}
                    onClick={() => setExpanded((e) => (e === s.def.id ? null : s.def.id))}
                    className="flex items-center gap-1 rounded-md text-sm font-medium text-ink hover:text-gold"
                  >
                    <ChevronRight className={cn('h-3.5 w-3.5 transition-transform', expanded === s.def.id && 'rotate-90')} aria-hidden />
                    {s.def.name}
                  </button>
                </div>
                <div className="order-3 col-span-2 sm:order-none sm:col-span-1">
                  <div className="group/track relative h-5 rounded-full bg-base-raised" aria-hidden>
                    {segments(s.intervals, dayStart).map((seg, i) => (
                      <span
                        key={i}
                        title={`${s.def.name}: ${formatInZone(seg.start, tz, HM)}–${formatInZone(seg.end, tz, HM)} ${tzLabel}`}
                        className={cn(
                          'absolute inset-y-1.5 rounded-full transition-all hover:inset-y-1',
                          s.isOpen ? 'bg-gold' : 'bg-line-control hover:bg-neutral',
                        )}
                        style={{ left: `${seg.left}%`, width: `${seg.width}%` }}
                      />
                    ))}
                    <span className="absolute inset-y-0 w-px bg-ink" style={{ left: `${nowPct}%` }} />
                  </div>
                </div>
                <div className="flex items-center justify-end gap-2">
                  {s.isOpen ? <Badge tone="up">Open</Badge> : <Badge>{s.isWeekend ? 'Weekend' : 'Closed'}</Badge>}
                </div>
                <div className="num col-span-2 flex flex-wrap justify-between gap-x-4 text-2xs text-neutral sm:col-span-3">
                  <span>
                    {shown ? `${formatInZone(shown.start, tz, HM)} – ${formatInZone(shown.end, tz, HM)} ${tzLabel}` : '—'}
                    {!s.isOpen && shown && (
                      <span className="ml-1">({formatInZone(shown.start, tz, { weekday: 'short' })})</span>
                    )}
                  </span>
                  <span className="text-ink-muted">
                    {s.isOpen ? 'Closes in ' : 'Opens in '}
                    {formatDuration(s.countdownMs)}
                  </span>
                </div>
                {(detailed || expanded === s.def.id) && (
                  <dl
                    id={`session-${s.def.id}`}
                    className="col-span-2 grid animate-fade-in grid-cols-2 gap-x-4 gap-y-1 rounded-lg bg-base-raised px-3 py-2 text-xs sm:col-span-3 sm:grid-cols-4"
                  >
                    <div>
                      <dt className="text-neutral">Local hours</dt>
                      <dd className="num text-ink">
                        {s.def.open}–{s.def.close}
                      </dd>
                    </div>
                    <div>
                      <dt className="text-neutral">Home market</dt>
                      <dd className="text-ink">{s.def.timeZone.split('/').pop()?.replace('_', ' ')}</dd>
                    </div>
                    <div>
                      <dt className="text-neutral">Length</dt>
                      <dd className="num text-ink">{shown ? formatDuration(shown.end - shown.start) : '—'}</dd>
                    </div>
                    <div>
                      <dt className="text-neutral">{s.isOpen ? 'Open for' : 'Closed since'}</dt>
                      <dd className="num text-ink">
                        {s.current ? formatDuration(now - s.current.start) : (() => {
                          const prev = [...s.intervals].reverse().find((i) => i.end <= now);
                          return prev ? formatDuration(now - prev.end) : '—';
                        })()}
                      </dd>
                    </div>
                  </dl>
                )}
              </li>
            );
          })}
        </ul>
        <div className="num flex justify-between text-2xs text-neutral" aria-hidden>
          {['00', '04', '08', '12', '16', '20', '24'].map((h) => (
            <span key={h}>{h}:00</span>
          ))}
        </div>
        <div className="flex flex-wrap items-center justify-between gap-2 border-t border-line-soft pt-3 text-xs">
          <span className="text-ink-muted">London / New York overlap</span>
          {overlapActive ? (
            <span className="flex items-center gap-2">
              <Badge tone="up">Active</Badge>
              <span className="num text-ink-muted">ends {formatInZone(overlapActive.end, tz, HM)}</span>
            </span>
          ) : nextOverlap ? (
            <span className="num text-ink-muted">
              Next {formatInZone(nextOverlap.start, tz, { weekday: 'short', ...HM })}–{formatInZone(nextOverlap.end, tz, HM)} · in{' '}
              {formatDuration(nextOverlap.start - now)}
            </span>
          ) : (
            <span className="text-neutral">—</span>
          )}
        </div>
      </CardBody>
    </Card>
  );
}
