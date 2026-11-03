import { useState } from 'react';
import { ChevronDown, ChevronUp } from 'lucide-react';
import { PageHeader } from '@/components/common/PageHeader';
import { Card } from '@/components/common/Card';
import { StatusIndicator } from '@/components/common/StatusIndicator';
import { DataSourceBadge } from '@/components/common/DataSourceBadge';
import { QueryState } from '@/components/common/QueryState';
import { Button } from '@/components/common/Button';
import { EmptyState } from '@/components/common/EmptyState';
import { useAgents } from '@/hooks/queries';
import { usePermission } from '@/hooks/usePermission';
import { formatDateTime, formatInteger, PLACEHOLDER } from '@/utils/format';
import type { AgentInfo } from '@/types/system';

function AgentCard({ a, canConfigure }: { a: AgentInfo; canConfigure: boolean }) {
  const [open, setOpen] = useState(false);
  return (
    <Card className="flex flex-col">
      <div className="flex items-start gap-3 p-4">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h2 className="text-sm font-semibold text-ink">{a.name}</h2>
            <StatusIndicator status={a.status} />
          </div>
          <p className="mt-1 text-xs leading-relaxed text-neutral">{a.description}</p>
        </div>
      </div>
      <dl className="grid grid-cols-3 border-y border-line-soft text-xs">
        <div className="border-r border-line-soft p-3">
          <dt className="eyebrow">Last run</dt>
          <dd className="num mt-1 truncate text-ink-muted">{a.lastExecution ? formatDateTime(a.lastExecution) : PLACEHOLDER}</dd>
        </div>
        <div className="border-r border-line-soft p-3">
          <dt className="eyebrow">Last result</dt>
          <dd className="mt-1 truncate text-ink-muted">{a.lastResult ?? PLACEHOLDER}</dd>
        </div>
        <div className="p-3">
          <dt className="eyebrow">Errors</dt>
          <dd className="num mt-1 text-ink-muted">{formatInteger(a.errorCount)}</dd>
        </div>
      </dl>
      <div className="flex items-center justify-between gap-2 p-3">
        <Button
          size="sm"
          variant="ghost"
          aria-expanded={open}
          icon={open ? <ChevronUp className="h-3.5 w-3.5" aria-hidden /> : <ChevronDown className="h-3.5 w-3.5" aria-hidden />}
          onClick={() => setOpen((o) => !o)}
        >
          Configuration & logs
        </Button>
        <Button size="sm" disabled disabledReason={canConfigure ? 'Agent control endpoints are not available yet' : 'Requires the Administrator role'}>
          Configure
        </Button>
      </div>
      {open && (
        <div className="border-t border-line-soft bg-base-raised p-3 text-xs">
          <p className="eyebrow mb-1.5">Configuration</p>
          <dl className="num mb-3 grid gap-1">
            {Object.entries(a.configuration).map(([k, v]) => (
              <div key={k} className="flex justify-between gap-2">
                <dt className="text-neutral">{k}</dt>
                <dd className="text-ink-muted">{String(v)}</dd>
              </div>
            ))}
          </dl>
          <p className="eyebrow mb-1.5">Activity log</p>
          {a.logs.length === 0 ? (
            <p className="text-neutral">No activity recorded.</p>
          ) : (
            <ul className="num max-h-40 space-y-1 overflow-y-auto">
              {a.logs.map((l, i) => (
                <li key={i} className={l.level === 'error' ? 'text-down-text' : l.level === 'warning' ? 'text-warn' : 'text-ink-muted'}>
                  {formatDateTime(l.timestamp)} · {l.message}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </Card>
  );
}

export default function AgentCentrePage() {
  const q = useAgents();
  const canConfigure = usePermission('config:agents');
  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        eyebrow="System"
        title="Agent Centre"
        description="Specialised AURUM AI agents. Status is shown only as reported by the backend; no agent is assumed to be running."
        actions={<DataSourceBadge source={q.data?.source} fetchedAt={q.data?.fetchedAt} />}
      />
      <QueryState query={q} isEmpty={(d) => d.data.length === 0} empty={<EmptyState title="No agents registered" />}>
        {(d) => (
          <div className="grid gap-4 md:grid-cols-2 2xl:grid-cols-4">
            {d.data.map((a) => (
              <AgentCard key={a.id} a={a} canConfigure={canConfigure} />
            ))}
          </div>
        )}
      </QueryState>
    </div>
  );
}
