import { useEffect, useState } from 'react';
import { Card, CardBody, CardHeader } from '@/components/common/Card';
import { Badge } from '@/components/common/Badge';
import { Select } from '@/components/common/Select';
import { DataTable, type Column } from '@/components/common/DataTable';
import { QueryState } from '@/components/common/QueryState';
import { useCotReportDetail, useCotReports } from '@/hooks/queries';
import type { CotReportDetail, CotReportType } from '@/types/cot';
import { formatDateTime, formatInteger } from '@/utils/format';
import { fixed, humanize, signed } from './cotFormat';

type Pos = CotReportDetail['positions'][number];
type Met = Record<string, unknown>;

/** I. Historical report explorer: pick a report date, inspect the original CFTC record and the calculated metrics. */
export function CotReportExplorer({ instrument, reportType, demo }: { instrument: string; reportType: CotReportType; demo: boolean }) {
  const reports = useCotReports({ instrument, reportType, page: 1, pageSize: 500 });
  const items = reports.data?.data.items ?? [];
  const [id, setId] = useState<number | null>(null);
  useEffect(() => setId(null), [instrument, reportType]);
  const selected = id ?? items[0]?.id ?? null;
  const detail = useCotReportDetail(demo ? null : selected);
  if (demo) {
    return (
      <Card>
        <CardHeader title="Historical report explorer" />
        <CardBody className="text-xs text-ink-muted">Not available in demo mode: the explorer shows original CFTC records stored by the backend.</CardBody>
      </Card>
    );
  }
  const posCols: Column<Pos>[] = [
    { key: 'cat', header: 'Category', cell: (r) => humanize(r.traderCategory) },
    { key: 'l', header: 'Long', align: 'right', cell: (r) => formatInteger(r.longPositions) },
    { key: 's', header: 'Short', align: 'right', cell: (r) => formatInteger(r.shortPositions) },
    { key: 'sp', header: 'Spreading', align: 'right', cell: (r) => formatInteger(r.spreadingPositions) },
    { key: 'n', header: 'Net', align: 'right', cell: (r) => signed(r.netPositions) },
  ];
  const metCols: Column<Met>[] = [
    { key: 'cat', header: 'Category', cell: (r) => humanize(String(r.traderCategory)) },
    { key: 'chg', header: 'Weekly Δ', align: 'right', cell: (r) => signed(r.weeklyNetChange as number | null) },
    { key: 'p36', header: 'Pct 36M', align: 'right', cell: (r) => fixed(r.historicalPercentile36m as number | null) },
    { key: 'idx', header: 'COT idx', align: 'right', cell: (r) => fixed(r.cotIndex as number | null) },
    { key: 'z', header: 'Z', align: 'right', cell: (r) => fixed(r.zScore as number | null, 2, true) },
    { key: 'cls', header: 'Classification', cell: (r) => humanize(r.positioningStrength as string | null) },
    { key: 'ver', header: 'Calc. version', cell: (r) => String(r.calculationVersion ?? '') },
  ];
  return (
    <Card>
      <CardHeader
        title="Historical report explorer"
        subtitle="Original CFTC record (verbatim field names) with the positions and metrics derived from it."
        actions={
          <Select
            label="Report date"
            hideLabel
            value={selected != null ? String(selected) : ''}
            onChange={(v) => setId(Number(v))}
            options={items.map((r) => ({ value: String(r.id), label: `${r.reportDate} (released ${r.releaseDate})` }))}
            className="w-64"
          />
        }
      />
      <CardBody>
        <QueryState query={detail} compact isEmpty={(d) => !d.data} empty={<p className="text-xs text-neutral">No stored reports for this contract.</p>}>
          {(d) => {
            const r = d.data as CotReportDetail;
            return (
              <div className="flex flex-col gap-4">
                <dl className="grid gap-x-6 gap-y-2 text-xs sm:grid-cols-2 xl:grid-cols-4">
                  {([
                    ['Contract', `${r.contractMarketName} (${r.contractCode})`],
                    ['Report date (positions as of)', r.reportDate],
                    ['Public release', `${formatDateTime(r.releaseTimestamp)} UTC · ${r.releaseMethod}${r.releaseUncertain ? ' · uncertain' : ''}`],
                    ['Retrieved', `${formatDateTime(r.retrievalTimestamp)} UTC · ${r.source}${r.sourceFile ? ` (${r.sourceFile})` : ''}`],
                    ['Open interest', formatInteger(r.openInterest)],
                    ['Revisions', String(r.revisions.length)],
                  ] as [string, string][]).map(([k, v]) => (
                    <div key={k}>
                      <dt className="eyebrow">{k}</dt>
                      <dd className="num mt-0.5 text-ink">{v}</dd>
                    </div>
                  ))}
                </dl>
                {r.releaseUncertain && <Badge tone="warn">Release date uncertain - availability uses a conservative rule</Badge>}
                <DataTable columns={posCols} rows={r.positions} rowKey={(p) => p.traderCategory} dense caption="Positions" />
                <DataTable columns={metCols} rows={r.metrics} rowKey={(m) => String(m.traderCategory)} dense caption="Calculated metrics" />
                <details className="rounded border border-line-soft px-3 py-2 text-xs">
                  <summary className="cursor-pointer text-ink">Original CFTC record ({Object.keys(r.sourcePayload).length} fields)</summary>
                  <dl className="num mt-2 grid max-h-80 gap-x-4 gap-y-0.5 overflow-auto sm:grid-cols-2">
                    {Object.entries(r.sourcePayload).map(([k, v]) => (
                      <div key={k} className="flex justify-between gap-3 border-b border-line-soft py-0.5">
                        <dt className="truncate text-neutral" title={k}>{k}</dt>
                        <dd className="text-ink">{String(v ?? '')}</dd>
                      </div>
                    ))}
                  </dl>
                </details>
              </div>
            );
          }}
        </QueryState>
      </CardBody>
    </Card>
  );
}
