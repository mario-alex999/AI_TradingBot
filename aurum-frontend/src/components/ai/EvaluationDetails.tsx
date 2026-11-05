import { Card, CardBody, CardHeader } from '@/components/common/Card';
import { DataTable, type Column } from '@/components/common/DataTable';
import { humanize } from '@/utils/labels';
import type { EvaluationDetails as Details, GroupStats } from '@/types/decision';

interface Row extends GroupStats {
  name: string;
}

const COLUMNS: Column<Row>[] = [
  { key: 'name', header: 'Group', cell: (r) => humanize(r.name), sortValue: (r) => r.name },
  { key: 'trades', header: 'Trades', cell: (r) => r.trades, sortValue: (r) => r.trades, align: 'right' },
  { key: 'win', header: 'Win rate', cell: (r) => `${r.winRate.toFixed(1)}%`, sortValue: (r) => r.winRate, align: 'right' },
  { key: 'exp', header: 'Expectancy (R)', cell: (r) => r.expectancyR.toFixed(2), sortValue: (r) => r.expectancyR, align: 'right' },
  { key: 'total', header: 'Total (R)', cell: (r) => r.totalR.toFixed(2), sortValue: (r) => r.totalR, align: 'right' },
];

function Group({ title, stats }: { title: string; stats: Record<string, GroupStats> }) {
  const rows = Object.entries(stats).map(([name, s]) => ({ name, ...s }));
  return (
    <Card>
      <CardHeader title={title} />
      <CardBody>
        <DataTable columns={COLUMNS} rows={rows} rowKey={(r) => r.name} caption={title} dense />
      </CardBody>
    </Card>
  );
}

/** The breakdowns behind a simulated report, with the assumptions it was run on. */
export function EvaluationDetails({ d }: { d: Details }) {
  return (
    <div className="flex flex-col gap-4">
      {d.warnings.length > 0 && (
        <ul className="list-inside list-disc rounded-card border border-warn/30 bg-warn-faint px-4 py-3 text-xs text-ink-muted">
          {d.warnings.map((w) => (
            <li key={w}>{w}</li>
          ))}
        </ul>
      )}
      <p className="text-xs text-neutral">
        Assumptions: spread from the stored candles, slippage {d.slippageR ?? 0} R per trade, {d.assumedRiskPct}% risk per trade on a {d.accountStart} account. AI reviewer: {d.aiReviewer ?? 'not used'}.
        Replay steps {d.steps ?? 0}, candidates seen {d.candidatesSeen ?? 0}, still open at the end {d.stillOpenAtEnd ?? 0}. {d.disclaimer}
      </p>
      <div className="grid gap-4 xl:grid-cols-2">
        <Group title="By strategy" stats={d.byTemplate} />
        <Group title="By market regime" stats={d.byRegime} />
        <Group title="By timeframe" stats={d.byTimeframe} />
        <Group title="By confidence" stats={d.byConfidence} />
      </div>
    </div>
  );
}
