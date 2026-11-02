import { Clock3 } from 'lucide-react';
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

function segments(intervals: Interval[], dayStart: number): { left: number; width: number }[] {
  const dayEnd = dayStart + 86_400_000;
  return intervals
    .map((i) => ({ s: Math.max(i.start, dayStart), e: Math.min(i.end, dayEnd) }))
    .filter((x) => x.e > x.s)
    .map((x) => ({ left: ((x.s - dayStart) / 86_400_000) * 100, width: ((x.e - x.s) / 86_400_000) * 100 }));
}

export function SessionPanel({ detailed = false }: { detailed?: boolean }) {
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
                  <span className="text-sm font-medium text-ink">{s.def.name}</span>
                </div>
                <div className="order-3 col-span-2 sm:order-none sm:col-span-1">
                  <div className="relative h-5 rounded bg-base-raised" aria-hidden>
                    {segments(s.intervals, dayStart).map((seg, i) => (
                      <span
                        key={i}
                        className={s.isOpen ? 'absolute inset-y-1 rounded-sm bg-gold/70' : 'absolute inset-y-1 rounded-sm bg-[#2c3d58]'}
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
                    {shown ? `${formatInZone(shown.start, tz, HM)} – ${formatInZone(shown.end, tz, HM)} ${tzLabel}` : '--'}
                    {!s.isOpen && shown && (
                      <span className="ml-1">({formatInZone(shown.start, tz, { weekday: 'short' })})</span>
                    )}
                  </span>
                  <span className="text-ink-muted">
                    {s.isOpen ? 'Closes in ' : 'Opens in '}
                    {formatDuration(s.countdownMs)}
                  </span>
                </div>
                {detailed && (
                  <p className="col-span-2 text-2xs text-neutral sm:col-span-3">
                    Local hours {s.def.open}–{s.def.close} ({s.def.timeZone})
                  </p>
                )}
              </li>
            );
          })}
        </ul>
        <div className="num flex justify-between text-[10px] text-neutral" aria-hidden>
          {['00', '04', '08', '12', '16', '20', '24'].map((h) => (
            <span key={h}>{h}:00</span>
          ))}
        </div>
        <div className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-line-soft bg-base-raised px-3 py-2 text-xs">
          <span className="text-ink-muted">London / New York overlap</span>
          {overlapActive ? (
            <span className="flex items-center gap-2">
              <Badge tone="gold">Active</Badge>
              <span className="num text-ink-muted">ends {formatInZone(overlapActive.end, tz, HM)}</span>
            </span>
          ) : nextOverlap ? (
            <span className="num text-ink-muted">
              Next {formatInZone(nextOverlap.start, tz, { weekday: 'short', ...HM })}–{formatInZone(nextOverlap.end, tz, HM)} · in{' '}
              {formatDuration(nextOverlap.start - now)}
            </span>
          ) : (
            <span className="text-neutral">--</span>
          )}
        </div>
      </CardBody>
    </Card>
  );
}
