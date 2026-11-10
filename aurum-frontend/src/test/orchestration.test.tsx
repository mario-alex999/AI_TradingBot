import { describe, expect, it } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import OrchestrationPage from '@/pages/OrchestrationPage';
import { mockApi } from './mockApi';
import { renderWithProviders } from './render';

type Cfg = { data?: string };

const ADMIN_PERMS = ['view:dashboard', 'orchestration:view', 'orchestration:trigger', 'orchestration:operate', 'orchestration:manage'];
const OBSERVER_PERMS = ['view:dashboard', 'orchestration:view'];
const server = (permissions: string[]) => ({ mode: 'server' as const, username: 'alice', serverRole: 'admin' as const, permissions, accountScope: null });

const EXEC = {
  executionId: 'e1234567-aaaa', workflowId: 'aurum-core-test-echo', workflowVersion: '1.0.0', environment: 'demo', status: 'FAILED',
  triggerType: 'manual', triggerSource: 'alice', correlationId: 'c-1', causationId: null, idempotencyKey: 'k', symbol: null, tradeIdeaId: null,
  approvalId: null, n8nExecutionId: '88', attempt: 1, retryCount: 0, nextAttemptAt: null, deadlineAt: null, errorCategory: 'WORKFLOW_ERROR',
  errorMessage: 'node failed', warnings: [], createdAt: '2026-11-10T09:00:00+00:00', startedAt: null, finishedAt: null, durationSeconds: null,
};
const OVERVIEW = {
  environment: 'demo', moduleVersion: '8.0.0', engine: {}, n8nPublicUrl: 'http://localhost:5678',
  workflows: { total: 5, active: 1, paused: 0, byState: { ACTIVE: 1 } },
  executions: { running: 0, queued: 0, retryPending: 0, succeeded: 3, failed: 1, deadLettered: 1 },
  last24h: { executions: 4, finished: 4, successRate: 0.75, avgDurationSeconds: 1.2, events: 12, avgDeliveryLatencyMs: 5, rejectedCallbacks: 0 },
  deadLettersOpen: 1, deliveriesPending: 0,
  integrations: { n8n: { status: 'DEGRADED', detail: 'API key not configured', checkedAt: null } },
  recentExecutions: [EXEC], alerts: [{ level: 'warning', message: '1 dead letter(s) need attention.' }],
};
const WF = {
  workflowId: 'aurum-core-test-echo', name: 'AURUM Core - Test echo', description: null, purpose: null, category: 'test', owner: null, environment: 'demo',
  engine: 'n8n', n8nWorkflowId: 'wf1', webhookPath: 'aurum-core-test-echo', state: 'ACTIVE', triggerTypes: ['manual'], activeVersionId: 'v1',
  activeVersion: '1.0.0', timeoutSeconds: 120, retryPolicy: {}, idempotentDispatch: true, placeholder: false, healthStatus: null, healthDetail: null,
  rowVersion: 3, createdAt: 'x', updatedAt: null, lastExecutionAt: null,
};
const DETAIL = { ...EXEC, input: { message: 'hi' }, output: null, timeline: [{ at: '2026-11-10T09:00:00+00:00', from: null, to: 'RECEIVED', by: 'alice', reason: null, attempt: 0 }], attempts: [], callbacks: [] };

const routes = (extra: Record<string, unknown> = {}) => ({
  'GET /orchestration/overview': OVERVIEW,
  'GET /orchestration/workflows': { items: [WF, { ...WF, workflowId: 'wf08-01-market-monitoring', name: 'WF08-01', state: 'REGISTERED', placeholder: true }] },
  'GET /orchestration/executions/e1234567-aaaa': DETAIL,
  ...extra,
});

describe('Module 08 orchestration page', () => {
  it('asks demo users to sign in instead of showing orchestration data', () => {
    renderWithProviders(<OrchestrationPage />, { mock: false });
    expect(screen.getByText('Sign in with a backend account')).toBeInTheDocument();
  });

  it('shows the overview from the backend, with alerts and the test-run control', async () => {
    mockApi(routes());
    renderWithProviders(<OrchestrationPage />, { mock: false, user: server(ADMIN_PERMS) });
    expect(await screen.findByText('1 dead letter(s) need attention.')).toBeInTheDocument();
    expect(screen.getByText('75%')).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole('button', { name: 'Run test' })).toBeEnabled());
    expect(screen.getAllByRole('tab')).toHaveLength(8);
  });

  it('hides actions from a view-only person', async () => {
    mockApi(routes());
    renderWithProviders(<OrchestrationPage />, { mock: false, user: server(OBSERVER_PERMS) });
    expect(await screen.findByText('1 dead letter(s) need attention.')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Run test' })).toBeNull();
    fireEvent.click(screen.getByRole('tab', { name: 'Workflow Registry' }));
    expect(await screen.findByText('Reserved (not built)')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Pause' })).toBeNull();
  });

  it('retries a failed execution only with a reason, and sends it to the backend', async () => {
    let body: Record<string, unknown> = {};
    mockApi(routes({
      'POST /orchestration/executions/e1234567-aaaa/retry': (c: Cfg) => {
        body = JSON.parse(c.data ?? '{}');
        return { status: 200, data: { ...DETAIL, status: 'WAITING_CALLBACK', attempt: 2 } };
      },
    }));
    renderWithProviders(<OrchestrationPage />, { mock: false, user: server(ADMIN_PERMS) });
    fireEvent.click(await screen.findByRole('row', { name: /Open execution e1234567/ }));
    const retry = await screen.findByRole('button', { name: 'Retry' });
    fireEvent.click(retry);
    const dialog = await screen.findByRole('dialog', { name: 'Retry execution' });
    const submit = within(dialog).getByRole('button', { name: 'Retry' });
    expect(submit).toBeDisabled();
    fireEvent.change(within(dialog).getByLabelText(/Reason/), { target: { value: 'fixed node' } });
    fireEvent.click(submit);
    await waitFor(() => expect(body).toEqual({ reason: 'fixed node', confirmUnknownOutcome: false }));
  });

  it('shows the server refusal for a business failure as is', async () => {
    mockApi(routes({
      'POST /orchestration/executions/e1234567-aaaa/retry': () => ({
        status: 409, data: { error: { code: 'BUSINESS_RULE_FAILURE', message: 'The workflow failed for a business reason (RISK_BLOCKED).' }, detail: 'The workflow failed for a business reason (RISK_BLOCKED).' },
      }),
    }));
    renderWithProviders(<OrchestrationPage />, { mock: false, user: server(ADMIN_PERMS) });
    fireEvent.click(await screen.findByRole('row', { name: /Open execution e1234567/ }));
    fireEvent.click(await screen.findByRole('button', { name: 'Retry' }));
    const dialog = await screen.findByRole('dialog', { name: 'Retry execution' });
    fireEvent.change(within(dialog).getByLabelText(/Reason/), { target: { value: 'again' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Retry' }));
    expect(await within(dialog).findByText(/business reason/)).toBeInTheDocument();
  });
});
