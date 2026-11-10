import { useState } from 'react';
import { PageHeader } from '@/components/common/PageHeader';
import { Badge } from '@/components/common/Badge';
import { DataSourceBadge } from '@/components/common/DataSourceBadge';
import { Tabs } from '@/components/common/Tabs';
import { SignInRequired } from '@/pages/ApprovalsPage';
import {
  AuditPanel,
  ConfigPanel,
  EventsPanel,
  ExecutionDrawer,
  ExecutionsPanel,
  FailuresPanel,
  IntegrationsPanel,
  OverviewPanel,
  WorkflowsPanel,
} from '@/components/orchestration/Panels';
import { useOrchOverview } from '@/hooks/useOrchestration';
import { useAuthStore } from '@/stores/authStore';

type Tab = 'overview' | 'workflows' | 'executions' | 'events' | 'failures' | 'integrations' | 'config' | 'audit';
const TABS: { id: Tab; label: string }[] = [
  { id: 'overview', label: 'Overview' },
  { id: 'workflows', label: 'Workflow Registry' },
  { id: 'executions', label: 'Execution Monitor' },
  { id: 'events', label: 'Event Monitor' },
  { id: 'failures', label: 'Failure Management' },
  { id: 'integrations', label: 'Integration Health' },
  { id: 'config', label: 'Configuration' },
  { id: 'audit', label: 'Audit' },
];

/**
 * Module 08 control plane for n8n workflows. It shows and coordinates workflow runs; it never approves a trade (Module 07)
 * or executes one (Module 09), and n8n never decides anything about risk or approval.
 */
export default function OrchestrationPage() {
  const user = useAuthStore((s) => s.user);
  const [tab, setTab] = useState<Tab>('overview');
  const [execution, setExecution] = useState<string | null>(null);
  const overview = useOrchOverview();
  const description =
    'Coordinates n8n workflows, events, retries and integration health. It never approves or executes trades: Module 06 decides risk, Module 07 records human approval, Module 09 executes.';

  if (user?.mode !== 'server') {
    return (
      <div className="flex flex-col gap-5">
        <PageHeader eyebrow="Others" title="Workflow Orchestration" description={description} />
        <SignInRequired what="Workflow orchestration controls" />
      </div>
    );
  }
  const o = overview.data?.data;
  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        eyebrow="Others"
        title="Workflow Orchestration"
        description={description}
        actions={
          <div className="flex items-center gap-2">
            {o && <Badge tone="info">{o.environment} · module {o.moduleVersion}</Badge>}
            <DataSourceBadge source={overview.data?.source} fetchedAt={overview.data?.fetchedAt} />
          </div>
        }
      />
      <Tabs label="Orchestration sections" tabs={TABS} value={tab} onChange={setTab} />
      <div role="tabpanel" id={`panel-${tab}`} aria-labelledby={`tab-${tab}`}>
        {tab === 'overview' && <OverviewPanel onOpenExecution={setExecution} />}
        {tab === 'workflows' && <WorkflowsPanel />}
        {tab === 'executions' && <ExecutionsPanel onOpen={setExecution} />}
        {tab === 'events' && <EventsPanel />}
        {tab === 'failures' && <FailuresPanel onOpenExecution={setExecution} />}
        {tab === 'integrations' && <IntegrationsPanel />}
        {tab === 'config' && <ConfigPanel />}
        {tab === 'audit' && <AuditPanel />}
      </div>
      <ExecutionDrawer id={execution} onClose={() => setExecution(null)} />
    </div>
  );
}
