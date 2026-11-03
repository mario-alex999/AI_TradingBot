import { Card, CardBody, CardHeader } from '@/components/common/Card';
import { Badge } from '@/components/common/Badge';
import { DataSourceBadge } from '@/components/common/DataSourceBadge';
import { InfoTip } from '@/components/common/InfoTip';
import { useMacroOverview, useMarketIntelligence, useVolatility } from '@/hooks/queries';
import { useSettingsStore } from '@/stores/settingsStore';
import { formatNumber } from '@/utils/format';
import type { MarketIntelligence, ScoreComponent } from '@/types/intelligence';
import { CoverageNotice } from './CalendarPanels';
import { MiQuery, MiTable, Provenance, Td, Th } from './miCommon';
import { fixed, fmtMinutes, fmtTime, humanize, IMPORTANCE_TONE, REGIME_TONE, RISK_TONE } from './miFormat';

function ScoreBlock({ label, score, info, children }: { label: string; score: number | null | undefined; info: string; children?: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-line-soft p-3">
      <span className="eyebrow flex items-center gap-1">
        {label}
        <InfoTip label={`About the ${label.toLowerCase()} score`}>{info}</InfoTip>
      </span>
      <p className="num mt-1 text-xl text-ink">{score == null ? 'n/a' : score.toFixed(0)}</p>
      {children}
    </div>
  );
}

function Components({ items }: { items: ScoreComponent[] }) {
  if (!items.length) return null;
  return (
    <ul className="mt-2 space-y-0.5 text-2xs text-neutral">
      {items.map((c) => (
        <li key={c.name} title={c.explanation}>
          {humanize(c.name)}: {c.available ? fixed(c.value, 2) : 'unavailable'}
          {c.weight != null && <span> (weight {c.weight})</span>}
        </li>
      ))}
    </ul>
  );
}

/** Panel C: market impact — event-risk state for an instrument, the relevant events, and the explainable scores. */
export function MarketImpactPanel({ symbol }: { symbol: string }) {
  const q = useMarketIntelligence(symbol);
  const tz = useSettingsStore((s) => s.displayTimezone);
  return (
    <Card>
      <CardHeader
        title={
          <span className="flex items-center gap-1.5">
            Market impact
            <InfoTip>
              Event risk combines scheduled events that affect this instrument with configured time windows. Scores are 0–100 indices with their
              components shown; they are not probabilities and not trade signals.
            </InfoTip>
          </span>
        }
        subtitle={`${symbol} · event risk and context scores`}
        actions={<DataSourceBadge source={q.data?.source} fetchedAt={q.data?.fetchedAt} />}
      />
      <MiQuery query={q}>
        {(r: MarketIntelligence) => {
          const er = r.eventRisk;
          const next = er.nextEvent;
          return (
            <>
              <CardBody className="flex flex-col gap-3 pt-2">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge tone={RISK_TONE[er.state] ?? 'neutral'}>{humanize(er.state)}</Badge>
                  <span className="text-xs text-ink-muted">{er.reason}</span>
                </div>
                {next && (
                  <p className="text-xs text-ink-muted">
                    Next relevant event: <span className="text-ink">{next.name}</span> ({next.currency}
                    {next.importance ? `, ${next.importance}` : ''}) at {fmtTime(next.scheduledAt, tz)} — in {fmtMinutes(next.minutesUntilRelease)}
                  </p>
                )}
                {er.activeEvents.length > 0 && (
                  <ul className="flex flex-col gap-1 text-xs">
                    {er.activeEvents.map((e) => (
                      <li key={e.id} className="flex flex-wrap items-center gap-2">
                        <Badge tone={RISK_TONE[e.state ?? 'UNKNOWN']}>{humanize(e.state)}</Badge>
                        {e.importance && <Badge tone={IMPORTANCE_TONE[e.importance]}>{e.importance}</Badge>}
                        <span className="text-ink">{e.name}</span>
                        <span className="num text-neutral">{fmtTime(e.scheduledAt, tz)}</span>
                      </li>
                    ))}
                  </ul>
                )}
                <div className="grid gap-3 sm:grid-cols-3">
                  <ScoreBlock label="Event risk" score={r.scores.eventRisk?.score} info="Importance × time-proximity of relevant events, adjusted by historical sensitivity and current volatility when available. n/a when the calendar state is unknown.">
                    <Components items={r.scores.eventRisk?.components ?? []} />
                  </ScoreBlock>
                  <ScoreBlock label="Session activity" score={r.scores.sessionActivity?.score} info={r.scores.sessionActivity?.explanation ?? 'Weighted session, overlap and measured activity components.'}>
                    <Components items={r.scores.sessionActivity?.components ?? []} />
                  </ScoreBlock>
                  <ScoreBlock label="Volatility" score={r.scores.volatility?.score} info={r.scores.volatility?.explanation ?? 'ATR percentile against recent history.'}>
                    <p className="mt-2 text-2xs text-neutral">Regime {humanize(r.scores.volatility?.regime)}</p>
                  </ScoreBlock>
                </div>
              </CardBody>
              <CoverageNotice coverage={er.coverage} />
              <div className="flex flex-wrap gap-2 px-4 pb-2 text-2xs">
                {Object.entries(r.integrations).map(([k, v]) => (
                  <Badge key={k} tone={v?.available ? 'up' : 'neutral'}>
                    {humanize(k.replace(/(\d+)/, ' $1'))}: {v?.available ? 'available' : 'unavailable'}
                  </Badge>
                ))}
              </div>
              <Provenance>
                Methodology {r.methodologyVersion} · as of {fmtTime(r.asOf, tz)}. {r.scores.disclaimer} {r.disclaimer}
              </Provenance>
            </>
          );
        }}
      </MiQuery>
    </Card>
  );
}

/** Panel G: market-condition tags, each with its evidence and source. */
export function MarketConditionsPanel({ symbol }: { symbol: string }) {
  const q = useMarketIntelligence(symbol);
  return (
    <Card>
      <CardHeader
        title={
          <span className="flex items-center gap-1.5">
            Market conditions
            <InfoTip>Several tags can apply at once. Each lists the facts and the module they came from. Confidence is a rule-based weight, not a probability.</InfoTip>
          </span>
        }
        subtitle={symbol}
        actions={<DataSourceBadge source={q.data?.source} fetchedAt={q.data?.fetchedAt} />}
      />
      <MiQuery query={q}>
        {(r: MarketIntelligence) => (
          <>
            <CardBody className="flex flex-col gap-3 pt-2">
              <div className="flex flex-wrap gap-2">
                {r.conditions.tags.map((t) => (
                  <Badge key={t} tone="gold">
                    {humanize(t.toLowerCase())}
                  </Badge>
                ))}
              </div>
              <ul className="flex flex-col gap-2">
                {r.conditions.conditions
                  .filter((c) => c.active)
                  .map((c) => (
                    <li key={c.tag} className="rounded-lg border border-line-soft p-3 text-xs">
                      <div className="flex items-center justify-between gap-2">
                        <span className="font-medium text-ink">{humanize(c.tag.toLowerCase())}</span>
                        <span className="num text-neutral">confidence {c.confidence.toFixed(2)}</span>
                      </div>
                      <ul className="mt-1.5 space-y-0.5 text-ink-muted">
                        {c.evidence.map((e, i) => (
                          <li key={i}>
                            {e.fact} <span className="text-2xs text-neutral">[{e.source}]</span>
                          </li>
                        ))}
                      </ul>
                    </li>
                  ))}
              </ul>
            </CardBody>
            <Provenance>
              {r.conditions.timeframe} efficiency ratio {fixed(r.conditions.efficiencyRatio, 2)}. {r.conditions.note}
            </Provenance>
          </>
        )}
      </MiQuery>
    </Card>
  );
}

/** Panel E: volatility regime, ATR / realised-volatility percentiles, session comparison. */
export function VolatilityPanel({ symbol, digits }: { symbol: string; digits: number }) {
  const q = useVolatility(symbol);
  const tz = useSettingsStore((s) => s.displayTimezone);
  return (
    <Card>
      <CardHeader
        title={
          <span className="flex items-center gap-1.5">
            Volatility
            <InfoTip>
              Regimes come from where current ATR sits among its own recent history (percentile cut-offs 10/30/70/90). Only closed candles are used. This
              describes the present; it is not a forecast.
            </InfoTip>
          </span>
        }
        subtitle={`${symbol} · calculated from stored candles`}
        actions={<DataSourceBadge source={q.data?.source} fetchedAt={q.data?.fetchedAt} />}
      />
      <MiQuery query={q}>
        {(v) => (
          <>
            <div className="flex flex-wrap items-center gap-2 px-4 pb-3 pt-2 text-xs">
              <Badge tone={REGIME_TONE[v.regime] ?? 'neutral'}>{humanize(v.regime.toLowerCase())}</Badge>
              <span className="text-ink-muted">{v.regimeReason}</span>
            </div>
            <MiTable
              caption="Volatility by timeframe"
              head={
                <>
                  <Th>TF</Th>
                  <Th right>ATR</Th>
                  <Th right>ATR pct.</Th>
                  <Th right>Realised vol</Th>
                  <Th right>Annualised</Th>
                  <Th>Expansion</Th>
                  <Th>Regime</Th>
                </>
              }
            >
              {Object.values(v.timeframes).map((t) => (
                <tr key={t.timeframe} title={t.reason}>
                  <th scope="row" className="px-3 py-2 text-left font-medium text-ink">
                    {t.timeframe}
                  </th>
                  <Td right>{formatNumber(t.atr, digits)}</Td>
                  <Td right>{fixed(t.atrPercentile, 0)}</Td>
                  <Td right>{t.realisedVolPct == null ? '—' : `${fixed(t.realisedVolPct, 3)}%`}</Td>
                  <Td right>{t.annualisedVolPct == null ? '—' : `${fixed(t.annualisedVolPct, 1)}%`}</Td>
                  <Td muted>
                    {humanize(t.expansionState)} {t.expansionRatio != null && <span className="num text-2xs">({fixed(t.expansionRatio, 2)})</span>}
                    {t.abnormalMovement && (
                      <Badge tone="down" className="ml-1">
                        abnormal
                      </Badge>
                    )}
                  </Td>
                  <Td>
                    <Badge tone={REGIME_TONE[t.regime] ?? 'neutral'}>{humanize(t.regime.toLowerCase())}</Badge>
                  </Td>
                </tr>
              ))}
            </MiTable>
            <div className="grid gap-3 px-4 py-3 text-xs sm:grid-cols-2">
              <p className="text-ink-muted">
                Today&apos;s range {formatNumber(v.daily?.currentRange, digits)} vs 20-day average {formatNumber(v.daily?.averageDailyRange20, digits)}
                {v.daily?.ratio != null && <span className="num"> ({fixed(v.daily.ratio, 2)}×)</span>}
                {v.daily?.provisional && <span className="text-neutral"> · provisional</span>}
              </p>
              <p className="text-ink-muted">
                This week {formatNumber(v.weekly?.currentRange, digits)} vs 10-week average {formatNumber(v.weekly?.averageWeeklyRange10, digits)}
                {v.weekly?.ratio != null && <span className="num"> ({fixed(v.weekly.ratio, 2)}×)</span>}
              </p>
            </div>
            <MiTable
              caption="Session volatility"
              head={
                <>
                  <Th>Session</Th>
                  <Th right>Latest range</Th>
                  <Th right>Mean range (20)</Th>
                  <Th right>Latest / mean</Th>
                  <Th right>Sample</Th>
                </>
              }
            >
              {Object.entries(v.sessions).map(([name, s]) => (
                <tr key={name}>
                  <th scope="row" className="px-3 py-2 text-left font-medium text-ink">
                    {humanize(name.replace(/([A-Z])/g, '_$1').toLowerCase())}
                  </th>
                  <Td right>{formatNumber(s.latestRange, digits)}</Td>
                  <Td right muted>{formatNumber(s.meanRange20, digits)}</Td>
                  <Td right>{s.latestVsMean == null ? '—' : `${fixed(s.latestVsMean, 2)}×`}</Td>
                  <Td right muted>{s.sampleSize}</Td>
                </tr>
              ))}
            </MiTable>
            <Provenance>As of {fmtTime(v.asOf, tz)}. Regime timeframe {v.regimeTimeframe}.</Provenance>
          </>
        )}
      </MiQuery>
    </Card>
  );
}

/** Panel F: central-bank policy rates and macro indicators from official sources, point-in-time. */
export function MacroPanel() {
  const q = useMacroOverview();
  const tz = useSettingsStore((s) => s.displayTimezone);
  return (
    <Card>
      <CardHeader
        title={
          <span className="flex items-center gap-1.5">
            Central banks & macro
            <InfoTip>
              Policy rates come from each central bank&apos;s official data service. Banks without an official machine-readable source are shown as not
              sourced instead of being estimated. Meeting times are typical announcement times from the official schedules.
            </InfoTip>
          </span>
        }
        subtitle="Official sources only"
        actions={<DataSourceBadge source={q.data?.source} fetchedAt={q.data?.fetchedAt} />}
      />
      <MiQuery query={q}>
        {(m) => (
          <>
            <MiTable
              caption="Central-bank policy rates"
              minWidth={920}
              head={
                <>
                  <Th>Bank</Th>
                  <Th right>Rate</Th>
                  <Th>In effect since</Th>
                  <Th right>Previous</Th>
                  <Th>Latest decision</Th>
                  <Th>Next meeting</Th>
                  <Th>Source</Th>
                </>
              }
            >
              {m.centralBanks.map((b) => (
                <tr key={b.bank}>
                  <th scope="row" className="px-3 py-2 text-left font-medium text-ink">
                    {b.bank.toUpperCase()} <span className="block text-2xs font-normal text-neutral">{b.currency}</span>
                  </th>
                  <Td right>
                    {b.currentRate == null
                      ? '—'
                      : b.currentRateLower != null
                        ? `${b.currentRateLower.toFixed(2)}–${b.currentRate.toFixed(2)}%`
                        : `${b.currentRate.toFixed(2)}%`}
                  </Td>
                  <Td muted>{b.lastChangeEffective ?? '—'}</Td>
                  <Td right muted>{b.previousRate == null ? '—' : `${b.previousRate.toFixed(2)}%`}</Td>
                  <Td muted>
                    {b.latestDecision ? (
                      <span className="text-xs">
                        {b.latestDecision.decisionDate} · {b.latestDecision.status}
                        {b.latestDecision.changeBps ? ` ${b.latestDecision.changeBps > 0 ? '+' : ''}${b.latestDecision.changeBps} bp` : ''}
                      </span>
                    ) : (
                      '—'
                    )}
                  </Td>
                  <Td muted>
                    <span className="text-xs">{b.nextMeeting ? `${fmtTime(b.nextMeeting.announcementUtc, tz)} (typical)` : '—'}</span>
                  </Td>
                  <Td muted>
                    {b.status === 'ok' ? (
                      <span className="text-2xs">
                        {b.source}
                        {b.stale && (
                          <Badge tone="warn" className="ml-1">
                            stale
                          </Badge>
                        )}
                      </span>
                    ) : (
                      <span title={b.reason ?? undefined}>
                        <Badge>{humanize(b.status)}</Badge>
                      </span>
                    )}
                  </Td>
                </tr>
              ))}
            </MiTable>
            <MiTable
              caption="Macro indicators"
              head={
                <>
                  <Th>Indicator</Th>
                  <Th>Period</Th>
                  <Th right>Latest</Th>
                  <Th right>Previous</Th>
                  <Th>Source</Th>
                  <Th>Status</Th>
                </>
              }
            >
              {m.indicators.map((i) => (
                <tr key={i.id}>
                  <th scope="row" className="px-3 py-2 text-left font-medium text-ink">
                    {i.name} <span className="block text-2xs font-normal text-neutral">{i.id}</span>
                  </th>
                  <Td muted>{i.latest?.period ?? '—'}</Td>
                  <Td right>{fixed(i.latest?.value, 2)}</Td>
                  <Td right muted>{fixed(i.previous?.value, 2)}</Td>
                  <Td muted>{i.source}</Td>
                  <Td>
                    <Badge tone={i.status === 'ok' ? 'up' : i.status === 'stale' ? 'warn' : 'neutral'}>{humanize(i.status)}</Badge>
                  </Td>
                </tr>
              ))}
            </MiTable>
            <Provenance>{m.note}</Provenance>
          </>
        )}
      </MiQuery>
    </Card>
  );
}
