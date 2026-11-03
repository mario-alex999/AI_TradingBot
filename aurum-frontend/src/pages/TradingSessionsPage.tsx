import { useMemo, useState } from 'react';
import { PageHeader } from '@/components/common/PageHeader';
import { Card, CardHeader } from '@/components/common/Card';
import { Select } from '@/components/common/Select';
import { Badge } from '@/components/common/Badge';
import { DataSourceBadge } from '@/components/common/DataSourceBadge';
import { QueryState } from '@/components/common/QueryState';
import { SessionPanel } from '@/components/dashboard/SessionPanel';
import { useCandles } from '@/hooks/queries';
import { useClock } from '@/hooks/useClock';
import { useSessions } from '@/hooks/useSessions';
import { useSettingsStore } from '@/stores/settingsStore';
import { DEFAULT_INSTRUMENTS } from '@/config/instruments';
import { demoProfile } from '@/mocks/market';
import { formatInZone, formatNumber, formatPercent, PLACEHOLDER } from '@/utils/format';
import { sessionStats } from '@/utils/sessionStats';
import { Link } from 'react-router-dom';

const HM: Intl.DateTimeFormatOptions = { weekday: 'short', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' };

export default function TradingSessionsPage() {
  const now = useClock(30_000);
  const tz = useSettingsStore((s) => s.displayTimezone);
  const [symbol, setSymbol] = useState('EURUSD');
  const candles = useCandles(symbol, 'M15', 1500);
  const { sessions } = useSessions(now);
  const digits = demoProfile(symbol).digits;

  const stats = useMemo(
    () => sessions.map((s) => ({ s, st: sessionStats(candles.data?.data ?? [], s.intervals, now) })),
    [sessions, candles.data, now],
  );

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        eyebrow="Intelligence"
        title="Trading Sessions"
        description={
          <>
            Session hours are defined in each market's own timezone, so daylight-saving changes are applied automatically. Configure sessions in{' '}
            <Link to="/settings?tab=market" className="text-ink underline decoration-line-control underline-offset-4 hover:decoration-ink">
              Settings
            </Link>
            .
          </>
        }
      />
      <SessionPanel detailed />
      <Card>
        <CardHeader
          title="Session ranges"
          subtitle="High, low and range derived from stored M15 candles. Volatility compares the latest range to the average of the previous 10 sessions."
          actions={
            <>
              <DataSourceBadge source={candles.data?.source} />
              <Select label="Instrument" hideLabel value={symbol} onChange={setSymbol} options={DEFAULT_INSTRUMENTS.map((i) => ({ value: i.symbol, label: i.symbol }))} className="w-32" />
            </>
          }
        />
        <QueryState query={candles}>
          {() => (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[920px] text-sm">
                <caption className="sr-only">Session ranges</caption>
                <thead>
                  <tr className="border-b border-line text-xs text-neutral">
                    {['Session', 'Opened', 'Closes / closed', 'High', 'Low', 'Range', 'Volatility', 'Liquidity', 'vs previous'].map((h, i) => (
                      <th key={h} scope="col" className={i > 2 ? 'px-3 py-2.5 text-right font-semibold' : 'px-3 py-2.5 text-left font-semibold'}>
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {stats.map(({ s, st }) => {
                    const r = st.latest;
                    return (
                      <tr key={s.def.id} className="border-b border-line-soft last:border-0">
                        <th scope="row" className="px-3 py-2.5 text-left font-medium text-ink">
                          <span className="flex items-center gap-2">
                            {s.def.name}
                            {s.isOpen && <Badge tone="up">Live</Badge>}
                          </span>
                        </th>
                        <td className="num px-3 py-2.5 text-ink-muted">{r ? formatInZone(r.interval.start, tz, HM) : PLACEHOLDER}</td>
                        <td className="num px-3 py-2.5 text-ink-muted">{r ? formatInZone(r.interval.end, tz, HM) : PLACEHOLDER}</td>
                        <td className="num px-3 py-2.5 text-right text-ink">{formatNumber(r?.high, digits)}</td>
                        <td className="num px-3 py-2.5 text-right text-ink">{formatNumber(r?.low, digits)}</td>
                        <td className="num px-3 py-2.5 text-right text-ink">{formatNumber(r?.range, digits)}</td>
                        <td className="num px-3 py-2.5 text-right">
                          {st.relativeVolatility == null ? (
                            PLACEHOLDER
                          ) : (
                            <Badge tone={st.relativeVolatility > 1.3 ? 'warn' : st.relativeVolatility < 0.7 ? 'info' : 'neutral'}>
                              {st.relativeVolatility.toFixed(2)}×
                            </Badge>
                          )}
                        </td>
                        <td className="px-3 py-2.5 text-right text-2xs text-neutral" title="Requires tick-volume/spread analytics from the backend">
                          Awaiting backend
                        </td>
                        <td className="num px-3 py-2.5 text-right text-ink-muted">{formatPercent(st.changeVsPrevious, 1, true)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </QueryState>
      </Card>
    </div>
  );
}
