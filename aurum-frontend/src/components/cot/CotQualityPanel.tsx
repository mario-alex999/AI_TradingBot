import { useState } from 'react';
import { Card, CardHeader } from '@/components/common/Card';
import { Badge } from '@/components/common/Badge';
import { DataTable, type Column } from '@/components/common/DataTable';
import { SegmentedControl } from '@/components/common/SegmentedControl';
import { QueryState } from '@/components/common/QueryState';
import { useCotLogs, useCotQuality } from '@/hooks/queries';
import type { CotIngestionLog, CotQualityIssue, CotSeverity, CotStatus } from '@/types/cot';
import { REPORT_TYPE_LABEL } from '@/types/cot';
import { formatDateTime, formatInteger } from '@/utils/format';
import { freshnessTone, humanize, severityTone } from './cotFormat';

/** H. Data quality and updates: freshness, ingestion history (failed updates), issues, running jobs. */
export function CotQualityPanel({ status, instrument }: { status: CotStatus | undefined; instrument: string }) {
  const [severity, setSeverity] = useState<CotSeverity | ''>('');
  const quality = useCotQuality({ page: 1, pageSize: 25, severity, instrument });
  const logs = useCotLogs(1, 12);
  const series = status?.series ?? [];
  const issueCols: Column<CotQualityIssue>[] = [
    { key: 'sev', header: 'Severity', cell: (r) => <Badge tone={severityTone(r.severity)}>{r.severity}</Badge> },
    { key: 'type', header: 'Issue', cell: (r) => humanize(r.issueType) },
    { key: 'rt', header: 'Report', cell: (r) => (r.reportType ? REPORT_TYPE_LABEL[r.reportType] : '—') },
    { key: 'date', header: 'Report date', cell: (r) => <span className="num">{r.reportDate ?? '—'}</span> },
    { key: 'desc', header: 'Explanation', cell: (r) => <span className="text-ink-muted">{r.description}</span> },
    { key: 'seen', header: 'Seen', align: 'right', cell: (r) => formatInteger(r.occurrences) },
  ];
  const logCols: Column<CotIngestionLog>[] = [
    { key: 'start', header: 'Started (UTC)', cell: (r) => <span className="num">{formatDateTime(r.startedAt)}</span> },
    { key: 'job', header: 'Job', cell: (r) => r.jobType },
    { key: 'rt', header: 'Report', cell: (r) => (r.reportType ? REPORT_TYPE_LABEL[r.reportType] : 'all') },
    { key: 'period', header: 'Period', cell: (r) => <span className="num">{r.periodStart ? `${r.periodStart} â†’ ${r.periodEnd}` : '—'}</span> },
    { key: 'src', header: 'Source', cell: (r) => r.source ?? '—' },
    { key: 'counts', header: 'Retrieved / new / revised / rejected', align: 'right', cell: (r) => <span className="num">{`${r.recordsRetrieved} / ${r.recordsInserted} / ${r.recordsUpdated} / ${r.recordsRejected}`}</span> },
    { key: 'status', header: 'Status', cell: (r) => <Badge tone={r.status === 'success' ? 'up' : r.status === 'running' ? 'info' : 'down'}>{r.status}</Badge> },
    { key: 'err', header: 'Error', cell: (r) => <span className="text-down-text">{r.errorMessage ?? ''}</span> },
  ];
  return (
    <div className="flex flex-col gap-4">
      <Card>
        <CardHeader title="Data freshness" subtitle={`Last successful update ${status?.lastSuccessfulIngestion ? formatDateTime(status.lastSuccessfulIngestion) + ' UTC' : '—'} · failed ingestions: ${status?.failedIngestions ?? 0}`} />
        <ul className="grid gap-2 p-4 sm:grid-cols-2 xl:grid-cols-4">
          {series.map((s) => (
            <li key={`${s.instrument}-${s.reportType}`} className="rounded border border-line-soft px-3 py-2 text-xs">
              <p className="flex items-center justify-between gap-2">
                <span className="num text-ink">{s.instrument} · {REPORT_TYPE_LABEL[s.reportType]}</span>
                <Badge tone={freshnessTone(s.freshness.status)}>{humanize(s.freshness.status)}</Badge>
              </p>
              <p className="num mt-1 text-neutral">
                {s.reports} reports · latest {s.latestReportDate ?? '—'} (released {s.latestReleaseDate ?? '—'})
              </p>
            </li>
          ))}
          {!series.length && <li className="text-xs text-neutral">No series stored yet. Run a historical backfill.</li>}
        </ul>
        {!!status?.runningJobs.length && (
          <div className="border-t border-line-soft px-4 py-3 text-xs" aria-label="Running jobs">
            {status.runningJobs.map((j) => (
              <p key={j.jobId} className="text-info">
                {j.type} job {j.status}: {j.progress.at(-1) ?? 'startingâ€¦'}
              </p>
            ))}
          </div>
        )}
      </Card>
      <Card>
        <CardHeader
          title={`Data-quality issues - ${instrument}`}
          subtitle="Unusual but legitimate changes are reported (info), never deleted."
          actions={
            <SegmentedControl
              label="Severity filter"
              value={severity || 'all'}
              onChange={(v) => setSeverity(v === 'all' ? '' : (v as CotSeverity))}
              options={[{ value: 'all', label: 'All' }, { value: 'critical', label: 'Critical' }, { value: 'error', label: 'Error' }, { value: 'warning', label: 'Warning' }, { value: 'info', label: 'Info' }]}
            />
          }
        />
        <QueryState query={quality} compact>
          {(d) => <DataTable columns={issueCols} rows={d.data.items} rowKey={(r) => String(r.id)} dense empty="No open issues for this instrument." caption="COT data-quality issues" />}
        </QueryState>
      </Card>
      <Card>
        <CardHeader title="Update and backfill history" subtitle="Every ingestion chunk is logged; failed chunks are retried by the next run." />
        <QueryState query={logs} compact>
          {(d) => <DataTable columns={logCols} rows={d.data.items} rowKey={(r) => String(r.id)} dense empty="No ingestion has run yet." caption="COT ingestion log" />}
        </QueryState>
      </Card>
    </div>
  );
}
