import { describe, expect, it } from 'vitest';
import { fireEvent, screen, waitFor } from '@testing-library/react';
import { Route, Routes } from 'react-router-dom';
import ApprovalsPage from '@/pages/ApprovalsPage';
import ApprovalDetailPage from '@/pages/ApprovalDetailPage';
import LoginPage from '@/pages/LoginPage';
import { useAuthStore } from '@/stores/authStore';
import { mockApi } from './mockApi';
import { renderWithProviders } from './render';

type Cfg = { data?: string; headers: Record<string, unknown> };

const PERMS = ['view:dashboard', 'approvals:view', 'approvals:decide', 'approvals:revalidate', 'approvals:cancel'];
const SERVER = { mode: 'server' as const, username: 'tom', serverRole: 'trader' as const, permissions: PERMS, accountScope: null };

const REQ = {
  approvalId: 'ap-1', ideaId: 'idea-1', ideaVersion: 1, parentApprovalId: null, status: 'UNDER_REVIEW', version: 2, symbol: 'EURUSD',
  direction: 'BUY', timeframe: 'H1', strategy: 'trend_pullback', entry: 1.085, stopLoss: 1.083, takeProfit: 1.09, positionSize: 0.45,
  riskAmount: 97.65, riskPct: 0.99, riskReward: 2.5, aiConfidence: 72, riskDecision: 'REVIEW', riskAssessmentId: 'ra-1',
  riskAssessedAt: '2026-11-09T10:00:00+00:00', riskConfigVersion: 3, riskWarnings: [{ ruleId: 'R041', message: 'News within 30 minutes.' }],
  evidenceSnapshotId: 'ev-1', evidenceHash: 'h-abc', analysisVersion: 'a1', policyVersion: 1, accountLogin: '1234', assignedUserId: null,
  approvalsRequired: 1, createdAt: '2026-11-09T10:00:00+00:00', expiresAt: new Date(Date.now() + 3_600_000).toISOString(), secondsRemaining: 3600,
  statusChangedAt: '2026-11-09T10:00:00+00:00', statusReason: null, decidedAt: null, canApprove: true, correlationId: 'c-1',
};

const detail = (over: Record<string, unknown> = {}) => ({
  request: { ...REQ, ...over },
  idea: { ideaId: 'idea-1', source: 'module05', sourceRef: null, market: 'forex', status: 'OPEN', currentVersion: 1, expiresAt: REQ.expiresAt },
  versions: [], decisions: [], riskAssessments: [], modifications: [], handoff: null, relatedRequests: [],
  disclaimer: 'Approval is not a guarantee of profit.',
});

const EVIDENCE = {
  snapshotId: 'ev-1', hash: 'h-abc', createdAt: 'x', completeness: {}, current: true, ideaId: 'idea-1', ideaVersion: 1, builtAt: 'x', analysisVersion: 'a1',
  sections: { trade: { name: 'Trade', source: 'module05', status: 'available', reason: null, asOf: null, items: [{ kind: 'confirmed', label: 'Entry', value: '1.08500', source: 'module05', asOf: null }] } },
};

const DETAIL_ROUTES = (over: Record<string, unknown> = {}, extra: Record<string, unknown> = {}) => ({
  'GET /approvals/ap-1': detail(over),
  'GET /approvals/ap-1/evidence': EVIDENCE,
  'GET /approvals/ap-1/history': { items: [] },
  'GET /approvals/policy': { policy: { review: { rejectionReasons: ['trader_discretion', 'risk_too_high', 'other'] } } },
  ...extra,
});

const renderDetail = () =>
  renderWithProviders(
    <Routes>
      <Route path="/approvals/:id" element={<ApprovalDetailPage />} />
    </Routes>,
    { route: '/approvals/ap-1', role: 'trader', mock: false, user: SERVER },
  );

const approveButton = () => screen.getByRole('button', { name: 'Approve trade' });

describe('Module 07 sign-in', () => {
  it('shows the username/password form when the backend has accounts, and signs in', async () => {
    let body: unknown;
    mockApi({
      'GET /auth/status': { ready: true, hasUsers: true },
      'POST /auth/login': (c: Cfg) => {
        body = JSON.parse(c.data ?? '{}');
        return { status: 200, data: { user: { id: 'u1', username: 'tom', displayName: 'Tom', role: 'trader', kind: 'human', permissions: PERMS, accountScope: null }, csrfToken: 'csrf-1', expiresAt: 'x', idleTimeoutMinutes: 30 } };
      },
    });
    renderWithProviders(<LoginPage />, { role: null, mock: false });
    fireEvent.change(await screen.findByLabelText('Username'), { target: { value: ' Tom ' } });
    fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'correct-horse-battery-9' } });
    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }));
    await waitFor(() => expect(useAuthStore.getState().user?.mode).toBe('server'));
    expect(body).toEqual({ username: 'tom', password: 'correct-horse-battery-9' });
    expect(useAuthStore.getState().user?.role).toBe('trader');
    // The password is never kept in the browser.
    expect(JSON.stringify(sessionStorage)).not.toContain('correct-horse');
  });

  it('shows a clear message for a wrong password', async () => {
    mockApi({
      'GET /auth/status': { ready: true, hasUsers: true },
      'POST /auth/login': () => ({ status: 401, data: { error: { code: 'INVALID_CREDENTIALS', message: 'no' }, detail: 'no' } }),
    });
    renderWithProviders(<LoginPage />, { role: null, mock: false });
    fireEvent.change(await screen.findByLabelText('Username'), { target: { value: 'tom' } });
    fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'wrong-password-123' } });
    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }));
    expect(await screen.findByText(/Username or password is incorrect/)).toBeInTheDocument();
    expect(useAuthStore.getState().user).toBeNull();
  });

  it('falls back to the demo workspace when the backend is unreachable', async () => {
    renderWithProviders(<LoginPage />, { role: null, mock: false });
    expect(await screen.findByText(/Backend sign-in is unavailable/)).toBeInTheDocument();
  });
});

describe('Module 07 approvals', () => {
  it('asks demo users to sign in with a backend account instead of showing approvals', () => {
    renderWithProviders(<ApprovalsPage />, { mock: false });
    expect(screen.getByText('Sign in with a backend account')).toBeInTheDocument();
  });

  it('lists pending requests from the backend for a server session', async () => {
    mockApi({
      'GET /approvals/pending': { items: [REQ], total: 1, page: 1, pageSize: 50 },
      'GET /approvals': { items: [REQ], total: 1, page: 1, pageSize: 50 },
      'GET /approvals/dashboard': null,
    });
    renderWithProviders(<ApprovalsPage />, { role: 'trader', mock: false, user: SERVER });
    expect(await screen.findByRole('row', { name: /Open BUY EURUSD request/ })).toBeInTheDocument();
  });

  it('keeps Approve disabled until evidence is loaded, warnings acknowledged and confirmation ticked, then sends the exact values', async () => {
    let sent: Record<string, unknown> | undefined;
    mockApi(
      DETAIL_ROUTES({}, {
        'POST /approvals/ap-1/approve': (c: Cfg) => {
          sent = JSON.parse(c.data ?? '{}');
          return { status: 200, data: { ...REQ, status: 'EXECUTION_PENDING', version: 3 } };
        },
      }),
    );
    renderDetail();
    await screen.findByText('Buy EURUSD');
    fireEvent.click(screen.getByRole('button', { name: /Approve…/ }));
    await waitFor(() => expect(approveButton()).toBeDisabled());
    fireEvent.click(screen.getByRole('checkbox', { name: /I have reviewed the evidence/ }));
    expect(approveButton()).toBeDisabled(); // warning not acknowledged yet
    expect(approveButton()).toHaveAttribute('title', 'Acknowledge every risk warning');
    fireEvent.click(screen.getByRole('checkbox', { name: /R041/ }));
    await waitFor(() => expect(approveButton()).toBeEnabled());
    fireEvent.click(approveButton());
    await waitFor(() => expect(sent).toBeDefined());
    expect(sent).toMatchObject({
      requestVersion: 2,
      evidenceHash: 'h-abc',
      acknowledgedWarnings: ['R041'],
      confirmation: { symbol: 'EURUSD', direction: 'BUY', entry: '1.085', stopLoss: '1.083', takeProfit: '1.09', positionSize: '0.45', maxRiskAmount: '97.65' },
    });
    expect(typeof sent?.clientRequestId).toBe('string');
  });

  it('shows every failed server check when the approval is refused', async () => {
    mockApi(
      DETAIL_ROUTES({ riskWarnings: [] }, {
        'POST /approvals/ap-1/approve': () => ({
          status: 409,
          data: { error: { code: 'APPROVAL_CHECKS_FAILED', message: 'Approval checks failed', details: ['Risk assessment expired', 'Request version changed'] }, detail: 'Approval checks failed' },
        }),
      }),
    );
    renderDetail();
    await screen.findByText('Buy EURUSD');
    fireEvent.click(screen.getByRole('button', { name: /Approve…/ }));
    fireEvent.click(screen.getByRole('checkbox', { name: /I have reviewed the evidence/ }));
    await waitFor(() => expect(approveButton()).toBeEnabled());
    fireEvent.click(approveButton());
    expect(await screen.findByText('The trade was not approved')).toBeInTheDocument();
    expect(screen.getByText('Risk assessment expired')).toBeInTheDocument();
    expect(screen.getByText('Request version changed')).toBeInTheDocument();
  });

  it('never allows approving a trade that Module 06 blocked', async () => {
    mockApi(DETAIL_ROUTES({ riskDecision: 'BLOCK', canApprove: false }));
    renderDetail();
    await screen.findByText('Buy EURUSD');
    const btn = screen.getByRole('button', { name: /Approve…/ });
    expect(btn).toBeDisabled();
    expect(btn).toHaveAttribute('title', expect.stringMatching(/blocked trade can never be approved/));
    expect(screen.getByRole('alert')).toHaveTextContent(/cannot be approved by anyone/);
  });

  it('requires a reason to reject, and an explanation for "other"', async () => {
    let sent: Record<string, unknown> | undefined;
    mockApi(
      DETAIL_ROUTES({}, {
        'POST /approvals/ap-1/reject': (c: Cfg) => ((sent = JSON.parse(c.data ?? '{}')), { status: 200, data: { ...REQ, status: 'REJECTED' } }),
      }),
    );
    renderDetail();
    await screen.findByText('Buy EURUSD');
    fireEvent.click(screen.getByRole('button', { name: /Reject…/ }));
    const reject = await screen.findByRole('button', { name: 'Reject' });
    expect(reject).toBeDisabled();
    await waitFor(() => expect(screen.getByRole('option', { name: 'Other (explain)' })).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText('Reason'), { target: { value: 'other' } });
    expect(reject).toBeDisabled();
    fireEvent.change(screen.getByLabelText(/Comment \(required\)/), { target: { value: 'Spread too wide' } });
    expect(reject).toBeEnabled();
    fireEvent.click(reject);
    await waitFor(() => expect(sent).toMatchObject({ reasonCode: 'other', comment: 'Spread too wide', requestVersion: 2 }));
  });

  it('gives observers no way to decide', async () => {
    mockApi(DETAIL_ROUTES());
    renderWithProviders(
      <Routes>
        <Route path="/approvals/:id" element={<ApprovalDetailPage />} />
      </Routes>,
      { route: '/approvals/ap-1', role: 'viewer', mock: false, user: { ...SERVER, serverRole: 'observer', permissions: ['view:dashboard', 'approvals:view'] } },
    );
    await screen.findByText('Buy EURUSD');
    expect(screen.getByRole('button', { name: /Approve…/ })).toHaveAttribute('title', 'Your role cannot approve trades');
    expect(screen.getByRole('button', { name: /Reject…/ })).toBeDisabled();
    expect(screen.queryByRole('button', { name: /Cancel request/ })).not.toBeInTheDocument();
  });
});
