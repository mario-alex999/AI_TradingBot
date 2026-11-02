import { Card, CardHeader } from '@/components/common/Card';
import { Badge } from '@/components/common/Badge';
import { MetricCard } from '@/components/common/MetricCard';
import { DataTable, type Column } from '@/components/common/DataTable';
import type { CotFamilyAnalysis, CotCategoryAnalysis } from '@/types/cot';
import { formatInteger } from '@/utils/format';
import { fixed, humanize, signed, strengthTone, valueTone } from './cotFormat';

/** C. Institutional positioning summary for one report family (CFTC facts + AURUM metrics, clearly labelled). */
export function CotSummary({ family }: { family: CotFamilyAnalysis }) {
  const cats = family.categories ?? [];
  const prim = cats.find((c) => c.category === family.primaryCategory);
  const columns: Column<CotCategoryAnalysis>[] = [
    { key: 'label', header: 'Trader category', cell: (r) => <span className="text-ink">{r.label}</span> },
    { key: 'long', header: 'Long', align: 'right', cell: (r) => formatInteger(r.long), sortValue: (r) => r.long },
    { key: 'short', header: 'Short', align: 'right', cell: (r) => formatInteger(r.short), sortValue: (r) => r.short },
    { key: 'spreading', header: 'Spreading', align: 'right', cell: (r) => formatInteger(r.spreading) },
    { key: 'net', header: 'Net', align: 'right', cell: (r) => <span className={r.netPosition && r.netPosition < 0 ? 'text-down-text' : 'text-up'}>{signed(r.netPosition)}</span>, sortValue: (r) => r.netPosition },
    { key: 'chg', header: 'Weekly Δ', align: 'right', cell: (r) => signed(r.weeklyNetChange), sortValue: (r) => r.weeklyNetChange },
    { key: 'oi', header: 'Net % OI', align: 'right', cell: (r) => fixed(r.netPositionPctOi, 1, true) },
    { key: 'p12', header: 'Pct 12M', align: 'right', cell: (r) => fixed(r.historicalPercentile12m) },
    { key: 'p36', header: 'Pct 36M', align: 'right', cell: (r) => fixed(r.historicalPercentile36m) },
    { key: 'idx', header: 'COT idx', align: 'right', cell: (r) => fixed(r.cotIndex) },
    { key: 'z', header: 'Z', align: 'right', cell: (r) => fixed(r.zScore, 2, true) },
    { key: 'cls', header: 'Classification', cell: (r) => (r.positioningStrength ? <Badge tone={strengthTone(r.positioningStrength)}>{humanize(r.positioningStrength)}</Badge> : '--') },
    { key: 'change', header: 'Change', cell: (r) => <span className="text-ink-muted">{humanize(r.changeType)}</span> },
  ];
  return (
    <div className="flex flex-col gap-3">
      <section aria-label="Primary group metrics" className="grid gap-3 sm:grid-cols-3 xl:grid-cols-6">
        <MetricCard label={`${prim?.label ?? 'Primary'} net`} value={signed(prim?.netPosition)} tone={valueTone(prim?.netPosition)} hint={`L ${formatInteger(prim?.long)} · S ${formatInteger(prim?.short)}`} />
        <MetricCard label="Weekly change" value={signed(prim?.weeklyNetChange)} tone={valueTone(prim?.weeklyNetChange)} hint={humanize(prim?.changeDriver)} />
        <MetricCard label="36M percentile" value={fixed(prim?.historicalPercentile36m)} tone={prim?.historicalPercentile36m == null ? 'muted' : 'flat'} hint={`12M ${fixed(prim?.historicalPercentile12m)} · 48M ${fixed(prim?.historicalPercentile48m)}`} />
        <MetricCard label="COT index (3Y)" value={fixed(prim?.cotIndex)} tone={prim?.cotIndex == null ? 'muted' : 'flat'} hint="0 = 3Y low · 100 = 3Y high" />
        <MetricCard label="Z-score (3Y)" value={fixed(prim?.zScore, 2, true)} tone={valueTone(prim?.zScore)} />
        <MetricCard
          label="Classification"
          value={<Badge tone={strengthTone(prim?.positioningStrength)} className="text-xs">{humanize(prim?.positioningStrength)}</Badge>}
          hint="Relative to own history - not a forecast"
        />
      </section>
      <Card>
        <CardHeader
          title="Trader categories"
          subtitle={`CFTC ${family.datasetName}: positions as of ${family.reportDate}, released ${family.releaseDate}. Long/short/spreading are CFTC figures; other columns are AURUM calculations.`}
        />
        <DataTable columns={columns} rows={cats} rowKey={(r) => r.category} dense caption={`${family.datasetName} positioning`} />
      </Card>
    </div>
  );
}
