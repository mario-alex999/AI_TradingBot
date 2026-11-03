import { Card, CardHeader } from '@/components/common/Card';
import { Badge, type Tone } from '@/components/common/Badge';
import { DataSourceBadge } from '@/components/common/DataSourceBadge';
import { InfoTip } from '@/components/common/InfoTip';
import { useBackendSessions, useSessionLevels, useSessionLiquidity } from '@/hooks/queries';
import { useSettingsStore } from '@/stores/settingsStore';
import { formatNumber } from '@/utils/format';
import type { SessionWindow, WindowStatus } from '@/types/intelligence';
import { MiQuery, MiTable, Provenance, Td, Th } from './miCommon';
import { fmtMinutes, fmtTime, humanize } from './miFormat';

const STATUS_TONE: Record<WindowStatus, Tone> = { open: 'up', pre_session: 'info', post_session: 'neutral', closed: 'neutral', market_closed: 'neutral' };

function WindowRow({ w, tz }: { w: SessionWindow; tz: string }) {
  const iv = w.current ?? w.next;
  return (
    <tr>
      <th scope="row" className="px-3 py-2 text-left font-medium text-ink">
        {humanize(w.name)}
      </th>
      <Td muted>
        {w.timezone}
        {w.dst && (
          <Badge tone="info" className="ml-1.5">
            DST
          </Badge>
        )}
      </Td>
      <Td muted>
        <span className="num">
          {w.localOpen}–{w.localClose}
        </span>{' '}
        <span className="text-2xs text-neutral">(now {w.localTime.slice(11, 16)})</span>
      </Td>
      <Td>
        <Badge tone={STATUS_TONE[w.status] ?? 'neutral'}>{humanize(w.status)}</Badge>
      </Td>
      <Td right muted>{fmtTime(iv?.startUtc, tz)}</Td>
      <Td right muted>{fmtTime(iv?.endUtc, tz)}</Td>
      <Td right>{w.current ? `closes in ${fmtMinutes(w.minutesUntilClose)}` : `opens in ${fmtMinutes(w.minutesUntilOpen)}`}</Td>
    </tr>
  );
}

/** Panel A: backend session engine (IANA time zones, DST, holidays, instrument trading hours). */
export function BackendSessionsPanel({ symbol }: { symbol?: string }) {
  const q = useBackendSessions(symbol);
  const tz = useSettingsStore((s) => s.displayTimezone);
  return (
    <Card>
      <CardHeader
        title={
          <span className="flex items-center gap-1.5">
            Session engine
            <InfoTip>
              Calculated on the backend from configured local hours in each market&apos;s IANA time zone, so daylight-saving changes, midnight crossings,
              weekends and configured holidays are handled server-side. Times are shown in your display time zone ({tz}).
            </InfoTip>
          </span>
        }
        subtitle={symbol ? `Trading hours of ${symbol} applied` : 'Sessions, kill zones and overlaps'}
        actions={<DataSourceBadge source={q.data?.source} fetchedAt={q.data?.fetchedAt} />}
      />
      <MiQuery query={q}>
        {(s) => (
          <>
            <div className="flex flex-wrap items-center gap-2 px-4 pb-3 pt-2 text-xs">
              <span className="text-neutral">Active:</span>
              {s.activeSessions.length ? s.activeSessions.map((n) => <Badge key={n} tone="up">{humanize(n)}</Badge>) : <Badge>None</Badge>}
              {s.inOverlap && <Badge tone="gold">Overlap</Badge>}
              {s.activeKillZones.map((n) => (
                <Badge key={n} tone="warn">
                  {humanize(n)}
                </Badge>
              ))}
              {s.marketOpen === false && <Badge tone="neutral">Instrument market closed</Badge>}
            </div>
            <MiTable
              caption="Session status"
              minWidth={860}
              head={
                <>
                  <Th>Window</Th>
                  <Th>Time zone</Th>
                  <Th>Local hours</Th>
                  <Th>Status</Th>
                  <Th right>Opens</Th>
                  <Th right>Closes</Th>
                  <Th right>Countdown</Th>
                </>
              }
            >
              {s.sessions.map((w) => (
                <WindowRow key={w.name} w={w} tz={tz} />
              ))}
              {s.killZones.map((w) => (
                <WindowRow key={w.name} w={w} tz={tz} />
              ))}
            </MiTable>
            <div className="grid gap-2 px-4 py-3 text-xs sm:grid-cols-3">
              {s.overlaps.map((o) => {
                const iv = o.current ?? o.next;
                return (
                  <div key={o.name} className="rounded-lg border border-line-soft p-3">
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-medium text-ink">{humanize(o.name)}</span>
                      <Badge tone={o.active ? 'gold' : 'neutral'}>{o.active ? 'Active' : humanize(o.kind)}</Badge>
                    </div>
                    <p className="num mt-1 text-neutral">
                      {fmtTime(iv?.startUtc, tz)} → {fmtTime(iv?.endUtc, tz)}
                    </p>
                  </div>
                );
              })}
            </div>
            <Provenance>
              Calculated at {fmtTime(s.asOf, tz)} · config {s.configFingerprint}. {s.note}
            </Provenance>
          </>
        )}
      </MiQuery>
    </Card>
  );
}

const LEVEL_TONE: Record<string, Tone> = { untouched: 'neutral', swept: 'warn', broken: 'info', provisional: 'gold' };

/** Session / day / week / month highs and lows with their state. */
export function SessionLevelsPanel({ symbol, digits }: { symbol: string; digits: number }) {
  const q = useSessionLevels(symbol);
  const tz = useSettingsStore((s) => s.displayTimezone);
  return (
    <Card>
      <CardHeader
        title={
          <span className="flex items-center gap-1.5">
            Session levels
            <InfoTip>
              Highs and lows of completed periods are final. Current-period levels are provisional and can still move. Swept means a wick traded
              beyond the level but closed back inside; broken means a candle closed beyond it.
            </InfoTip>
          </span>
        }
        subtitle={`${symbol} · calculated from stored Module 01 candles`}
        actions={<DataSourceBadge source={q.data?.source} fetchedAt={q.data?.fetchedAt} />}
      />
      <MiQuery query={q}>
        {(r) => (
          <>
            <MiTable
              caption="Session levels"
              minWidth={820}
              head={
                <>
                  <Th>Level</Th>
                  <Th right>Price</Th>
                  <Th>Period</Th>
                  <Th>Source</Th>
                  <Th>State</Th>
                  <Th right>Swept / broken</Th>
                </>
              }
            >
              {r.levels.map((l) => (
                <tr key={l.levelType}>
                  <th scope="row" className="px-3 py-2 text-left font-medium text-ink">
                    {humanize(l.levelType)}
                  </th>
                  <Td right>{formatNumber(l.price, digits)}</Td>
                  <Td muted>
                    <span className="num text-xs">
                      {fmtTime(l.periodStart, tz)} → {fmtTime(l.periodEnd, tz)}
                    </span>
                  </Td>
                  <Td muted>
                    <span className="text-xs">
                      {l.timeframe} · {fmtTime(l.sourceCandleTime, tz)}
                      {l.missingCandles ? <span className="text-warn"> · {l.missingCandles} missing</span> : null}
                    </span>
                  </Td>
                  <Td>
                    <Badge tone={LEVEL_TONE[l.state] ?? 'neutral'}>{humanize(l.state)}</Badge>
                  </Td>
                  <Td right muted>{fmtTime(l.sweptAt ?? l.brokenAt, tz)}</Td>
                </tr>
              ))}
            </MiTable>
            {r.issues.length > 0 && (
              <ul className="px-4 pt-2 text-2xs text-warn">
                {r.issues.map((i, k) => (
                  <li key={`${i.issue}-${i.level ?? k}`}>{i.message ?? `${humanize(i.issue)}${i.level ? ` (${humanize(i.level)})` : ''}`}</li>
                ))}
              </ul>
            )}
            <Provenance>As of {fmtTime(r.asOf, tz)}. {r.rules}</Provenance>
          </>
        )}
      </MiQuery>
    </Card>
  );
}

const LIQ_TONE: Record<string, Tone> = { wick_sweep: 'warn', confirmed_breakout: 'info', false_breakout_candidate: 'down', unconfirmed: 'neutral', range_expansion: 'gold' };

/** Panel D: session liquidity interactions, linked to Module 03 structure events where available. */
export function SessionLiquidityPanel({ symbol, digits }: { symbol: string; digits: number }) {
  const q = useSessionLiquidity(symbol);
  const tz = useSettingsStore((s) => s.displayTimezone);
  return (
    <Card>
      <CardHeader
        title={
          <span className="flex items-center gap-1.5">
            Session liquidity
            <InfoTip>
              How price interacted with earlier session and day/week levels: wick sweeps, confirmed breakouts, false-breakout candidates and range
              expansion. These are descriptions of what happened, not trade signals.
            </InfoTip>
          </span>
        }
        subtitle={`${symbol} · rule-based classification`}
        actions={<DataSourceBadge source={q.data?.source} fetchedAt={q.data?.fetchedAt} />}
      />
      <MiQuery query={q}>
        {(r) => (
          <>
            {r.events.length === 0 ? (
              <p className="px-4 py-6 text-center text-xs text-neutral">No session-level interactions in the lookback window.</p>
            ) : (
              <MiTable
                caption="Session liquidity events"
                minWidth={900}
                head={
                  <>
                    <Th>Time</Th>
                    <Th>Event</Th>
                    <Th>Session</Th>
                    <Th>Level</Th>
                    <Th right>Price</Th>
                    <Th>Module 03 structure</Th>
                  </>
                }
              >
                {r.events.map((e, i) => (
                  <tr key={`${e.eventTime}-${e.levelType}-${i}`} title={e.interpretation}>
                    <Td muted>
                      <span className="num text-xs">{fmtTime(e.eventTime, tz)}</span>
                    </Td>
                    <Td>
                      <Badge tone={LIQ_TONE[e.eventType] ?? 'neutral'}>{humanize(e.eventType)}</Badge>
                    </Td>
                    <Td muted>{humanize(e.session)}</Td>
                    <Td muted>{humanize(e.levelType)}</Td>
                    <Td right>{formatNumber(e.level, digits)}</Td>
                    <Td muted>
                      <span className="text-xs">
                        {e.relatedStructureEvent
                          ? `${humanize(String(e.relatedStructureEvent.eventType ?? 'event'))} ${e.relatedStructureEvent.direction ?? ''} (${e.relatedStructureEvent.timeframe ?? ''})`
                          : '—'}
                      </span>
                    </Td>
                  </tr>
                ))}
              </MiTable>
            )}
            <Provenance>
              Module 03 link: {r.module03.available ? `available (${r.module03.timeframe})` : `unavailable${r.module03.reason ? ` — ${r.module03.reason}` : ''}`}.{' '}
              {r.interpretation}
            </Provenance>
          </>
        )}
      </MiQuery>
    </Card>
  );
}
