import { PlugZap } from 'lucide-react';
import { Card, CardBody, CardHeader } from '@/components/common/Card';
import { StatusIndicator } from '@/components/common/StatusIndicator';
import { Skeleton } from '@/components/common/Skeleton';
import { describeError } from '@/utils/errors';
import { useHealth } from '@/hooks/queries';
import { env } from '@/config/env';
import { PLACEHOLDER, relativeTime } from '@/utils/format';

export function ConnectionStatusPanel() {
  const health = useHealth();
  const h = health.data;
  const down = health.isError;
  const items: [string, React.ReactNode][] = [
    ['MT5 connection', <StatusIndicator key="mt5" status={down ? 'disconnected' : (h?.mt5 ?? 'unknown')} />],
    ['Account environment', h?.environment === 'live' ? 'Live' : h?.environment === 'demo' ? 'Demo' : PLACEHOLDER],
    ['Broker', h?.brokerName ?? PLACEHOLDER],
    ['Terminal version', h?.terminalVersion ?? PLACEHOLDER],
    ['Database connection', <StatusIndicator key="db" status={down ? 'disconnected' : (h?.database ?? 'unknown')} />],
    ['Backend', down ? describeError(health.error).title : h ? `${h.status.toUpperCase()}${h.version ? ` · v${h.version}` : ''}` : PLACEHOLDER],
  ];
  return (
    <Card>
      <CardHeader
        icon={<PlugZap className="h-4 w-4" />}
        title="Connection status"
        subtitle={<span className="num">{env.apiBaseUrl}/health</span>}
        actions={h && <span className="text-2xs text-neutral">checked {relativeTime(h.checkedAt)}</span>}
      />
      <CardBody>
        <dl className="grid gap-x-6 sm:grid-cols-2 xl:grid-cols-3">
          {items.map(([k, v]) => (
            <div key={k} className="flex items-center justify-between gap-3 border-b border-line-soft py-2.5">
              <dt className="text-xs text-neutral">{k}</dt>
              <dd className="num text-xs text-ink">{health.isPending ? <Skeleton className="h-3 w-20" /> : v}</dd>
            </div>
          ))}
        </dl>
      </CardBody>
    </Card>
  );
}
