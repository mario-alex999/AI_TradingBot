import { FileText, Sigma, Sparkles } from 'lucide-react';
import { Card, CardBody, CardHeader } from '@/components/common/Card';
import { Badge } from '@/components/common/Badge';
import type { CotAnalysis, CotFamilyAnalysis } from '@/types/cot';
import type { DataSource } from '@/types/api';
import { formatDateTime } from '@/utils/format';
import { freshnessTone, humanize, signed } from './cotFormat';

const SECTIONS: { key: 'facts' | 'metrics' | 'interpretation'; title: string; note: string; icon: React.ReactNode }[] = [
  { key: 'facts', title: 'Measured data (CFTC)', note: 'Reported by the CFTC, unchanged.', icon: <FileText className="h-3.5 w-3.5" /> },
  { key: 'metrics', title: 'Calculated metrics (AURUM)', note: 'Deterministic calculations - see methodology.', icon: <Sigma className="h-3.5 w-3.5" /> },
  { key: 'interpretation', title: 'Interpretation', note: 'Rule-based reading of the metrics. Not a forecast or signal.', icon: <Sparkles className="h-3.5 w-3.5" /> },
];

/** F. Institutional intelligence for one report family. Never rendered from demo data. */
export function CotIntelligence({ analysis, family, source }: { analysis: CotAnalysis | null | undefined; family: CotFamilyAnalysis | undefined; source?: DataSource }) {
  if (source === 'demo' || !analysis) {
    return (
      <Card>
        <CardHeader icon={<Sparkles className="h-4 w-4" />} title="Institutional intelligence" actions={<Badge>Not generated</Badge>} />
        <CardBody>
          <p className="rounded-md border border-line-soft bg-base-raised px-3 py-2 text-xs text-ink-muted">
            {source === 'demo' ? 'Not generated: interpretations are never produced from demo data.' : 'Not generated: no COT analysis is available for this instrument.'}
          </p>
        </CardBody>
      </Card>
    );
  }
  if (!family?.available) {
    return (
      <Card>
        <CardHeader icon={<Sparkles className="h-4 w-4" />} title="Institutional intelligence" actions={<Badge tone="warn">Unavailable</Badge>} />
        <CardBody className="text-xs text-ink-muted">{family?.reason ?? 'No data stored for this report family.'}</CardBody>
      </Card>
    );
  }
  const conf = family.confidence;
  return (
    <Card>
      <CardHeader
        icon={<Sparkles className="h-4 w-4" />}
        title="Institutional intelligence"
        subtitle={`${family.datasetName} · report ${family.reportDate} · released ${family.releaseDate} · generated ${formatDateTime(analysis.generatedAt)} UTC`}
        actions={
          <>
            <Badge tone={freshnessTone(family.freshness?.status)}>{humanize(family.freshness?.status)}</Badge>
            <Badge tone={conf?.level === 'high' ? 'up' : conf?.level === 'medium' ? 'warn' : 'down'}>{conf?.level} confidence</Badge>
          </>
        }
      />
      <CardBody className="flex flex-col gap-4">
        {SECTIONS.map((s) => (
          <section key={s.key} aria-label={s.title}>
            <h3 className="mb-1.5 flex items-center gap-1.5 text-xs font-semibold text-ink">
              <span className="text-neutral" aria-hidden>{s.icon}</span>
              {s.title} <span className="font-normal text-neutral">- {s.note}</span>
            </h3>
            <ul className="flex list-disc flex-col gap-1 pl-5 text-xs text-ink-muted">
              {(family.statements?.[s.key] ?? []).map((line, i) => (
                <li key={i}>{line}</li>
              ))}
              {!family.statements?.[s.key]?.length && <li className="list-none text-neutral">No statements.</li>}
            </ul>
          </section>
        ))}
        {!!family.comparisons?.length && (
          <section aria-label="Participant comparisons">
            <h3 className="mb-1.5 text-xs font-semibold text-ink">Participant comparisons (same report family only)</h3>
            <ul className="grid gap-2 text-xs sm:grid-cols-2">
              {family.comparisons.map((c) => (
                <li key={c.label} className="rounded border border-line-soft px-3 py-2">
                  <p className="text-ink">{c.label}</p>
                  <p className="num text-neutral">
                    {signed(c.groupANet)} vs {signed(c.groupBNet)} · {humanize(c.positioningRelation)}
                    {c.flowRelation ? ` · ${humanize(c.flowRelation)}` : ''}
                  </p>
                </li>
              ))}
            </ul>
          </section>
        )}
        {(conf?.reasons.length || family.dataQualityWarnings?.length) ? (
          <section aria-label="Limitations" className="rounded-md border border-warn/30 bg-warn-faint px-3 py-2 text-xs text-ink-muted">
            <p className="font-semibold text-warn">Limitations</p>
            <ul className="mt-1 list-disc pl-5">
              {conf?.reasons.map((r) => <li key={r}>{r}</li>)}
              {family.dataQualityWarnings?.slice(0, 5).map((w, i) => <li key={`w${i}`}>{w.description}</li>)}
            </ul>
          </section>
        ) : null}
        <p className="text-2xs text-neutral">{analysis.disclaimer}</p>
      </CardBody>
    </Card>
  );
}
