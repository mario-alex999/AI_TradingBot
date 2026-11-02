import { Activity, RefreshCw } from 'lucide-react';
import { Card, CardBody, CardHeader } from '@/components/common/Card';
import { Badge } from '@/components/common/Badge';
import { Button } from '@/components/common/Button';
import type { CotStatus } from '@/types/cot';
import { formatDateTime, formatInteger, PLACEHOLDER } from '@/utils/format';

interface Props {
  status: CotStatus | undefined;
  demo: boolean;
  probing: boolean;
  onProbe: () => void;
}

/** A. COT engine overview - status, CFTC connectivity, freshness and counts (stored data is shown even offline). */
export function CotOverview({ status, demo, probing, onProbe }: Props) {
  const latestReport = status?.datasets.map((d) => d.latestReportDate).sort().at(-1);
  const latestRelease = status?.datasets.map((d) => d.latestReleaseDate).sort().at(-1);
  const reports = status?.datasets.reduce((n, d) => n + d.reports, 0) ?? 0;
  const issues = status?.openIssues ?? {};
  const alerts = (issues.critical ?? 0) + (issues.error ?? 0) + (issues.warning ?? 0);
  const cftc = status?.cftc;
  const cftcText = demo
    ? 'Demo mode'
    : !cftc?.checked
      ? 'Not checked'
      : cftc.apiReachable
        ? 'API reachable'
        : cftc.filesReachable
          ? 'API blocked - official files OK'
          : 'Unreachable';
  const cftcTone = demo || !cftc?.checked ? 'neutral' : cftc.apiReachable || cftc.filesReachable ? 'up' : 'down';
  const items: [string, React.ReactNode][] = [
    ['Engine', <Badge key="e" tone={demo ? 'warn' : status?.engine === 'ok' ? 'up' : 'warn'}>{demo ? 'Demo data' : status?.engine === 'ok' ? 'Operational' : 'No data'}</Badge>],
    ['CFTC source', <Badge key="c" tone={cftcTone}>{cftcText}</Badge>],
    ['Last successful update', status?.lastSuccessfulIngestion ? `${formatDateTime(status.lastSuccessfulIngestion)} UTC` : PLACEHOLDER],
    ['Latest report (as of)', latestReport ?? PLACEHOLDER],
    ['Latest public release', latestRelease ?? PLACEHOLDER],
    ['Contracts monitored', formatInteger(status?.contractsMonitored)],
    ['Reports stored', formatInteger(reports)],
    ['Data-quality alerts', <span key="q" className={alerts ? 'text-warn' : 'text-ink'}>{formatInteger(alerts)}{status?.pendingUpdates ? ` · ${status.pendingUpdates} update(s) due` : ''}</span>],
  ];
  return (
    <Card>
      <CardHeader
        icon={<Activity className="h-4 w-4" />}
        title="COT engine overview"
        subtitle={`Source: ${status?.configuredSource ?? PLACEHOLDER}. Reports describe positions as of Tuesday; they are usable only from the CFTC release (normally Friday 15:30 ET).`}
        actions={
          !demo && (
            <Button size="sm" variant="secondary" onClick={onProbe} disabled={probing}>
              <RefreshCw className={probing ? 'h-3.5 w-3.5 animate-spin' : 'h-3.5 w-3.5'} aria-hidden /> Test CFTC connection
            </Button>
          )
        }
      />
      <CardBody>
        <dl className="grid gap-x-6 gap-y-3 sm:grid-cols-2 xl:grid-cols-4">
          {items.map(([k, v]) => (
            <div key={k}>
              <dt className="eyebrow">{k}</dt>
              <dd className="num mt-1 text-sm text-ink">{v}</dd>
            </div>
          ))}
        </dl>
        {cftc?.checked && cftc.sources && (
          <ul className="mt-3 flex flex-wrap gap-2 text-2xs text-neutral" aria-label="CFTC connectivity by dataset">
            {cftc.sources.map((s) => (
              <li key={`${s.dataset}-${s.source}`} className="rounded border border-line-soft px-2 py-1">
                {s.dataset} · {s.source}: <span className={s.ok ? 'text-up' : 'text-down-text'}>{s.ok ? 'ok' : 'unavailable'}</span>
              </li>
            ))}
          </ul>
        )}
      </CardBody>
    </Card>
  );
}
