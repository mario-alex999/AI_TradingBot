import { Info } from 'lucide-react';
import { Link } from 'react-router-dom';
import { PageHeader } from '@/components/common/PageHeader';
import { Card, CardBody, CardHeader } from '@/components/common/Card';
import { Badge } from '@/components/common/Badge';
import { DataSourceBadge } from '@/components/common/DataSourceBadge';
import { ErrorState } from '@/components/common/ErrorState';
import { SkeletonRows } from '@/components/common/Skeleton';
import { useCotAnalysisReport } from '@/hooks/queries';
import { DEFAULT_INSTRUMENTS } from '@/config/instruments';
import { REPORT_TYPE_LABEL } from '@/types/cot';
import { fixed, freshnessTone, humanize, signed, strengthTone } from '@/components/cot/cotFormat';

/** Cross-instrument institutional positioning overview (backend COT intelligence; never from demo data). */
function InstrumentIntelligence({ symbol }: { symbol: string }) {
  const q = useCotAnalysisReport(symbol);
  const a = q.data?.data;
  const demo = q.data?.source === 'demo';
  return (
    <Card>
      <CardHeader
        title={symbol}
        subtitle={a ? a.families.map((f) => `${REPORT_TYPE_LABEL[f.reportType]}: ${f.contract.name}`).join(' · ') : undefined}
        actions={
          a && !demo ? (
            <>
              <Badge tone={a.overall.stance === 'bullish' ? 'up' : a.overall.stance === 'bearish' ? 'down' : a.overall.stance === 'mixed' ? 'warn' : 'neutral'}>
                {a.overall.stance}
                {a.overall.lean ? ` · leaning ${a.overall.lean}` : ''}
              </Badge>
              <Badge>{a.overall.confidence} confidence</Badge>
            </>
          ) : null
        }
      />
      <CardBody className="flex flex-col gap-3 text-xs">
        {q.isPending ? (
          <SkeletonRows rows={3} />
        ) : q.isError ? (
          <ErrorState error={q.error} onRetry={() => void q.refetch()} compact />
        ) : demo || !a ? (
          <p className="text-ink-muted">Not generated: interpretations are never produced from demo data. Connect the AURUM backend to see CFTC-based intelligence.</p>
        ) : (
          <>
            {a.families.map((f) =>
              !f.available ? (
                <p key={f.reportType} className="text-neutral">{REPORT_TYPE_LABEL[f.reportType]}: {f.reason}</p>
              ) : (
                <div key={f.reportType} className="rounded border border-line-soft px-3 py-2">
                  <p className="flex flex-wrap items-center justify-between gap-2">
                    <span className="text-ink">{REPORT_TYPE_LABEL[f.reportType]} · report {f.reportDate} · released {f.releaseDate}</span>
                    <Badge tone={freshnessTone(f.freshness?.status)}>{humanize(f.freshness?.status)}</Badge>
                  </p>
                  {(() => {
                    const p = f.categories?.find((c) => c.category === f.primaryCategory);
                    return p ? (
                      <p className="num mt-1 text-ink-muted">
                        {p.label}: net {signed(p.netPosition)} (Δ {signed(p.weeklyNetChange)}) · pct 36M {fixed(p.historicalPercentile36m)} · COT idx {fixed(p.cotIndex)} ·{' '}
                        <Badge tone={strengthTone(p.positioningStrength)}>{humanize(p.positioningStrength)}</Badge>
                      </p>
                    ) : null;
                  })()}
                  <ul className="mt-1 list-disc pl-5 text-ink-muted">
                    {f.statements?.interpretation.slice(0, 3).map((s, i) => <li key={i}>{s}</li>)}
                  </ul>
                </div>
              ),
            )}
            <p className="text-ink-muted">{a.overall.interpretation}</p>
          </>
        )}
        <Link to="/cot" className="text-2xs text-gold hover:underline">
          Open full COT analysis →
        </Link>
      </CardBody>
    </Card>
  );
}

export default function InstitutionalIntelligencePage() {
  const first = useCotAnalysisReport(DEFAULT_INSTRUMENTS[0]?.symbol ?? 'EURUSD');
  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        eyebrow="Intelligence"
        title="Institutional Intelligence"
        description="Positioning of large market participants from CFTC Commitments of Traders reports, interpreted relative to each group's own history."
        actions={<DataSourceBadge source={first.data?.source} fetchedAt={first.data?.fetchedAt} />}
      />
      <div className="flex gap-2 rounded-card border border-info/30 bg-info-faint px-4 py-3 text-xs text-ink-muted">
        <Info className="mt-0.5 h-4 w-4 shrink-0 text-info" aria-hidden />
        <p>
          COT reports show <strong className="text-ink">open positions</strong> by trader category - not individual institutions and not realised profit or loss. Non-reportable traders
          are those below CFTC reporting thresholds, not an exact measure of retail traders. Statements are positioning context, never predictions.
        </p>
      </div>
      <div className="grid grid-cols-1 gap-5 xl:grid-cols-2">
        {DEFAULT_INSTRUMENTS.map((i) => (
          <InstrumentIntelligence key={i.symbol} symbol={i.symbol} />
        ))}
      </div>
    </div>
  );
}
