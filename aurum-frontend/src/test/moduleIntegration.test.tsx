import { afterEach, describe, expect, it } from 'vitest';
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { CommandCentre } from '@/components/dashboard/CommandCentre';
import { AdminJobButton } from '@/components/common/AdminJobButton';
import { useAdminKeyStore } from '@/stores/adminKeyStore';
import { mockApi } from './mockApi';
import { renderWithProviders } from './render';

const SERVER = { mode: 'server' as const, username: 'tom', serverRole: 'trader' as const, permissions: ['approvals:view'], accountScope: null };
const inHours = (h: number) => new Date(Date.now() + h * 3_600_000).toISOString();

afterEach(() => useAdminKeyStore.getState().clear());

describe('Dashboard command centre', () => {
  it('summarises approvals, risk, news, decisions and workflows from their modules', async () => {
    mockApi({
      'GET /approvals/dashboard': {
        counts: { PENDING: 2, UNDER_REVIEW: 1 }, pending: [], managementPending: 0, recentHandoffs: [],
        statistics: { total: 9, approved: 4, rejected: 2, expired: 0, invalidated: 0, executed: 0, executionFailed: 0 },
      },
      'GET /risk/health': { engineVersion: '6.0.0', status: 'ok', canAssess: true, configVersion: 1, checkedAt: inHours(0), note: '', dependencies: {} },
      'GET /risk/locks': { active: [], history: [] },
      'GET /intelligence/news-impact': {
        generatedAt: inHours(0), method: 'news_impact_v1', reactionMinutes: 60, disclaimer: 'x', window: { start: inHours(-24), end: inHours(168) },
        importance: ['high'], events: [], headlines: [],
        nextEvent: { id: 1, name: 'CPI (YoY)', currency: 'USD', scheduledAt: inHours(1.5), released: false, scenarios: [], instruments: [] },
      },
      'GET /decision/status': {
        schemaReady: true, engineVersion: '5.0.0', counts: { new: 3, under_review: 1, rejected: 5 }, disclaimer: '',
        recentRuns: [{ symbol: 'EURUSD', asOf: inHours(-1), mode: 'live', status: 'ok', durationMs: 900, error: null, createdAt: inHours(-1) }],
        aiReviewer: { enabled: false, blockedReason: 'disabled', model: null, budgetUsd: null, budgetUsedUsd: 0 },
      },
      'GET /orchestration/overview': {
        environment: 'dev', moduleVersion: '8', workflows: { total: 6, active: 5, paused: 1, byState: {} },
        executions: { running: 1, queued: 0, retryPending: 0, succeeded: 40, failed: 0, deadLettered: 0 }, last24h: {}, engine: {},
        n8nPublicUrl: '', deadLettersOpen: 0, deliveriesPending: 0, integrations: {}, recentExecutions: [], alerts: [],
      },
    });
    renderWithProviders(<CommandCentre />, { mock: false, user: SERVER });
    await waitFor(() => expect(within(screen.getByTestId('cc-approvals')).getByText('3')).toBeInTheDocument());
    expect(screen.getByTestId('cc-approvals')).toHaveTextContent('3 ideas waiting for your decision');
    expect(screen.getByTestId('cc-risk')).toHaveTextContent('Ready');
    expect(screen.getByTestId('cc-news')).toHaveTextContent('USD in 1h');
    expect(screen.getByTestId('cc-news')).toHaveTextContent('CPI (YoY)');
    expect(screen.getByTestId('cc-ai-engine')).toHaveTextContent('4 candidates');
    expect(screen.getByTestId('cc-orchestration')).toHaveTextContent('5 active');
    expect(screen.getByTestId('cc-orchestration')).toHaveTextContent('1 running · no failures');
  });

  it('says why a module has nothing to show instead of inventing numbers', async () => {
    renderWithProviders(<CommandCentre />);
    await waitFor(() => expect(screen.getByTestId('cc-news')).toHaveTextContent('Needs the backend (demo mode)'));
    expect(screen.getByTestId('cc-approvals')).toHaveTextContent('—');
  });
});

describe('Admin data actions', () => {
  it('is unavailable in demo mode and without the admin key', async () => {
    renderWithProviders(<AdminJobButton action="newsRefresh" invalidate={['news-impact']} />);
    expect(await screen.findByRole('button', { name: /refresh news/i })).toBeDisabled();
  });

  it('starts the job with the admin key and follows it to completion', async () => {
    let sentKey: unknown;
    let polled = 0;
    mockApi({
      'POST /intelligence/news/refresh': (config: { headers: Record<string, unknown> }) => {
        sentKey = config.headers['X-AURUM-Admin-Key'];
        return { status: 202, data: { jobId: 'j1', type: 'intelligence.news.refresh', status: 'queued' } };
      },
      'GET /intelligence/jobs/j1': () => {
        polled += 1;
        return { status: 200, data: { jobId: 'j1', type: 'x', status: 'succeeded', progress: ['12 headlines stored'], result: {}, error: null } };
      },
    });
    act(() => useAdminKeyStore.getState().setKey('secret'));
    renderWithProviders(<AdminJobButton action="newsRefresh" invalidate={['news-impact']} />, { mock: false });
    fireEvent.click(await screen.findByRole('button', { name: /refresh news/i }));
    await waitFor(() => expect(sentKey).toBe('secret'));
    await waitFor(() => expect(polled).toBeGreaterThan(0));
    await waitFor(() => expect(screen.getByRole('button', { name: /refresh news/i })).not.toBeDisabled());
  });
});
