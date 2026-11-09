import { afterEach, describe, expect, it } from 'vitest';
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import RiskManagementPage from '@/pages/RiskManagementPage';
import { useAdminKeyStore } from '@/stores/adminKeyStore';
import { mockApi } from './mockApi';
import { renderWithProviders } from './render';

type Cfg = { headers: Record<string, unknown>; data?: string; params?: Record<string, unknown> };

const PERIOD = (label: string, lossPct: number, limitPct: number) => ({
  label, start: '2026-11-03T22:00:00+00:00', end: '2026-11-04T22:00:00+00:00', startValue: 10000, startSource: 'snapshot', startUncertain: false,
  currentValue: 10000 - lossPct * 100, netDeposits: 0, realizedPnl: 0, loss: lossPct * 100, lossPct, limitPct,
});

const ACCOUNT = {
  overallStatus: 'WITHIN_LIMITS', configVersion: 3, profile: 'moderate', activeLocks: 0, dataStatus: 'ok', reason: null, ageMinutes: 1, lossBasis: 'equity',
  capturedAt: '2026-11-04T12:00:00+00:00', currency: 'USD', tradeMode: 'DEMO', leverage: 100, balance: 10000, equity: 9850, floatingPnl: -150, margin: 500,
  freeMargin: 9350, marginLevel: 1970, marginUtilizationPct: 5, peakEquity: 10200, drawdownPct: 3.43, maxDrawdownPct: 10,
  periods: { day: PERIOD('2026-11-04', 1.5, 3), week: PERIOD('2026-W45', 1.5, 6), month: PERIOD('2026-11', 1.5, 10) },
  consecutiveLosses: 1, maxConsecutiveLosses: 3, streakTrades: [], recentResults: [], tradesToday: 2, maxTradesPerDay: 6, computedAt: '2026-11-04T12:00:00+00:00',
};

const CONFIG = {
  version: 3,
  meta: { version: 3, profile: 'moderate', fingerprint: 'x', createdBy: 'ops', createdAt: '2026-11-04T10:00:00+00:00', reason: 'tighten', weakened: [] },
  config: {
    profile: 'moderate', riskPerTradePct: 1, maxRiskPerTradePct: 2, maxDailyLossPct: 3, maxWeeklyLossPct: 6, maxMonthlyLossPct: 10, maxDrawdownPct: 10, maxOpenPositions: 5,
    maxTotalOpenRiskPct: 3, maxSymbolRiskPct: 2, maxAssetClassRiskPct: 3, maxCorrelatedRiskPct: 2, maxConsecutiveLosses: 3, maxTradesPerDay: 6, minRiskReward: 1.5,
    maxMarginUtilizationPct: 30, lossBasis: 'equity', tradingDay: { timezone: 'America/New_York', rollover: '17:00' }, assessmentTtlMinutes: 15, resetPolicy: {},
  },
  profiles: ['conservative', 'moderate', 'aggressive', 'custom'],
  history: [{ version: 3, profile: 'moderate', fingerprint: 'x', createdBy: 'ops', createdAt: '2026-11-04T10:00:00+00:00', reason: 'tighten', weakened: [] }],
};

const CALC = {
  accountCurrency: 'USD', valuePerPriceUnitPerLot: 100000, valueMethod: 'contract_size', conversion: null, stopDistance: 0.002, takeProfitDistance: 0.005,
  spreadPrice: 0.00012, slippagePrice: 0.00005, commissionPerLot: 0, lossPerLotAtStop: 200, costPerLot: 17, totalLossPerLot: 217, riskReward: 2.5,
  adjustedRiskReward: 2.25, riskPctBudget: 1, maxRiskAmount: 98.5, rawVolume: 0.4539, recommendedVolume: 0.45, finalVolume: 0.45, finalVolumeSource: 'recommended',
  cappedByMaxVolume: false, minVolumeRiskPct: 0.022, estimatedLoss: 97.65, estimatedLossPct: 0.99, estimatedReward: 219.6, transactionCost: 7.65, notional: 49504,
  marginEstimate: 495, marginMethod: 'estimate', notes: [],
};

const SIZE = {
  symbol: 'EURUSD', direction: 'BUY', valid: true, equitySource: 'account_mirror', accountCurrency: 'USD', configVersion: 3, calculation: CALC, checks: [],
  failedChecks: [], warnings: [], note: 'What-if calculation only.', disclaimer: 'Estimated risk at the stop-loss.',
};

const VIOLATION = { ruleId: 'R030', name: 'Daily loss limit', category: 'account', severity: 'HARD_BLOCK', outcome: 'fail', reasonCode: 'DAILY_LOSS_LIMIT', message: 'Daily loss 3.1% reached the 3% limit.', evidence: {}, thresholds: {} };

const ASSESSMENT = {
  assessmentId: 'a1b2c3d4-0000', decision: 'BLOCK', instrument: 'EURUSD', direction: 'BUY', strategy: null, recommendedVolume: 0.45, finalVolume: null, riskPct: null,
  monetaryRisk: null, potentialReward: null, riskReward: null, accountCurrency: 'USD', calculation: CALC, ruleViolations: [VIOLATION], warnings: [], errors: [],
  information: [], activeLocks: [], configVersion: 3, configProfile: 'moderate', engineVersion: '6.0.0', timestamp: '2026-11-04T12:00:00+00:00',
  expiresAt: '2026-11-04T12:15:00+00:00', correlationId: 'c', createdBy: 'admin-key', state: 'active', replayed: false,
  module07: { eligibleForHumanReview: false, fullyRiskApproved: false, mustDisplayWarnings: false, hardBlock: true, note: 'Module 07 cannot override BLOCK or ERROR.' },
  disclaimer: 'x',
};

const BASE = {
  'GET /risk/account': ACCOUNT,
  'GET /risk/health': { engineVersion: '6.0.0', status: 'ok', canAssess: true, configVersion: 3, checkedAt: 'x', note: '', dependencies: {} },
  'GET /risk/locks': { active: [], history: [] },
  'GET /risk/config': CONFIG,
};

afterEach(() => useAdminKeyStore.getState().clear());

describe('Module 06 risk dashboard', () => {
  it('shows the account from the backend with limits used', async () => {
    mockApi(BASE);
    renderWithProviders(<RiskManagementPage />, { mock: false });
    expect(within(await screen.findByRole('status')).getByText('Within limits')).toBeInTheDocument();
    expect(screen.getByText('$9,850.00')).toBeInTheDocument();
    expect(screen.getByRole('meter', { name: /Daily loss/ })).toHaveAttribute('aria-valuetext', '1.50% of 3.00%');
    expect(screen.getByText('Engine 6.0.0 · ok')).toBeInTheDocument();
  });

  it('says why when account data is unavailable instead of showing zeros', async () => {
    mockApi({ ...BASE, 'GET /risk/account': { ...ACCOUNT, overallStatus: 'DATA_UNAVAILABLE', dataStatus: 'missing', reason: 'No account snapshot is stored.', equity: null, balance: null } });
    renderWithProviders(<RiskManagementPage />, { mock: false });
    expect(await screen.findByText('No account snapshot is stored.')).toBeInTheDocument();
    expect(within(screen.getByRole('status')).getByText('Data unavailable')).toBeInTheDocument();
  });

  it('sizes a trade on the backend and shows its numbers, not browser maths', async () => {
    let body: unknown;
    mockApi({ ...BASE, 'POST /risk/position-size': (c: Cfg) => ((body = JSON.parse(c.data ?? '{}')), { status: 200, data: SIZE }) });
    renderWithProviders(<RiskManagementPage />, { mock: false });
    fireEvent.change(await screen.findByLabelText('Entry price'), { target: { value: '1.10010' } });
    fireEvent.change(screen.getByLabelText('Stop-loss (required)'), { target: { value: '1.09810' } });
    fireEvent.change(screen.getByLabelText('Take-profit'), { target: { value: '1.10510' } });
    fireEvent.click(screen.getByRole('button', { name: 'Calculate size' }));
    expect(await screen.findByTestId('engine-lots')).toHaveTextContent('0.45 lots');
    expect(body).toEqual({ symbol: 'EURUSD', direction: 'BUY', entry: 1.1001, stopLoss: 1.0981, takeProfit: 1.1051 });
  });

  it('refuses to calculate without a stop-loss', async () => {
    let called = false;
    mockApi({ ...BASE, 'POST /risk/position-size': () => ((called = true), { status: 200, data: SIZE }) });
    renderWithProviders(<RiskManagementPage />, { mock: false });
    fireEvent.change(await screen.findByLabelText('Entry price'), { target: { value: '1.1' } });
    fireEvent.click(screen.getByRole('button', { name: 'Calculate size' }));
    expect(await screen.findByText('A stop-loss is required')).toBeInTheDocument();
    expect(called).toBe(false);
  });

  it('runs a full assessment only with the admin key and shows the block reason', async () => {
    let key: unknown;
    mockApi({ ...BASE, 'POST /risk/assess': (c: Cfg) => ((key = c.headers['X-AURUM-Admin-Key']), { status: 201, data: ASSESSMENT }) });
    renderWithProviders(<RiskManagementPage />, { mock: false });
    expect(await screen.findByRole('button', { name: 'Run full assessment' })).toBeDisabled();
    act(() => useAdminKeyStore.getState().setKey('secret'));
    fireEvent.change(screen.getByLabelText('Entry price'), { target: { value: '1.10010' } });
    fireEvent.change(screen.getByLabelText('Stop-loss (required)'), { target: { value: '1.09810' } });
    fireEvent.click(screen.getByRole('button', { name: 'Run full assessment' }));
    const result = await screen.findByLabelText('Assessment result');
    expect(within(result).getByText('Block')).toBeInTheDocument();
    expect(within(result).getByText(/Daily loss 3.1% reached the 3% limit./)).toBeInTheDocument();
    expect(key).toBe('secret');
  });

  it('activates a kill switch with a reason and the admin key', async () => {
    let sent: { key: unknown; body: unknown } | undefined;
    mockApi({ ...BASE, 'POST /risk/locks': (c: Cfg) => ((sent = { key: c.headers['X-AURUM-Admin-Key'], body: JSON.parse(c.data ?? '{}') }), { status: 201, data: {} }) });
    act(() => useAdminKeyStore.getState().setKey('secret'));
    renderWithProviders(<RiskManagementPage />, { mock: false });
    fireEvent.click(await screen.findByRole('button', { name: 'Activate lock' }));
    const form = screen.getByRole('form', { name: 'Activate lock' });
    const submit = within(form).getByRole('button', { name: 'Lock' });
    expect(submit).toBeDisabled();
    fireEvent.change(within(form).getByLabelText('Reason'), { target: { value: 'News spike, stop all' } });
    fireEvent.click(submit);
    await waitFor(() => expect(sent).toEqual({ key: 'secret', body: { scope: 'global', reason: 'News spike, stop all' } }));
  });

  it('releases an active lock only with a written reason', async () => {
    let body: unknown;
    const lock = {
      lockId: 'L1', scope: 'global', scopeValue: null, reason: 'Daily loss limit reached', triggerSource: 'rule', ruleId: 'R030', status: 'active',
      activatedAt: '2026-11-04T11:00:00+00:00', activatedBy: 'system', expiresAt: null, releasedAt: null, releasedBy: null, releaseReason: null, history: [],
    };
    mockApi({ ...BASE, 'GET /risk/locks': { active: [lock], history: [lock] }, 'POST /risk/locks/L1/release': (c: Cfg) => ((body = JSON.parse(c.data ?? '{}')), { status: 200, data: {} }) });
    act(() => useAdminKeyStore.getState().setKey('secret'));
    renderWithProviders(<RiskManagementPage />, { mock: false });
    expect(await screen.findByText('Daily loss limit reached')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Release…' }));
    const release = screen.getByRole('button', { name: 'Release lock' });
    expect(release).toBeDisabled();
    fireEvent.change(screen.getByLabelText(/Release reason/), { target: { value: 'Reviewed with the desk' } });
    fireEvent.click(release);
    await waitFor(() => expect(body).toEqual({ reason: 'Reviewed with the desk' }));
  });

  it('asks for a second confirmation before loosening a limit', async () => {
    const bodies: Record<string, unknown>[] = [];
    mockApi({
      ...BASE,
      'PUT /risk/config': (c: Cfg) => {
        const b = JSON.parse(c.data ?? '{}');
        bodies.push(b);
        return b.acknowledgeWeakening
          ? { status: 200, data: { ...CONFIG, version: 4 } }
          : { status: 409, data: { error: { code: 'WEAKENING_NOT_ACKNOWLEDGED', message: 'Loosens max_daily_loss_pct: 3 -> 5' }, detail: 'Loosens max_daily_loss_pct: 3 -> 5' } };
      },
    });
    act(() => useAdminKeyStore.getState().setKey('secret'));
    renderWithProviders(<RiskManagementPage />, { mock: false });
    fireEvent.click(await screen.findByRole('tab', { name: 'Settings' }));
    fireEvent.change(await screen.findByLabelText(/Daily loss limit/), { target: { value: '5' } });
    fireEvent.change(screen.getByLabelText(/Reason for the change/), { target: { value: 'Wider daily room' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save new version' }));
    expect(await screen.findByText('Loosens max_daily_loss_pct: 3 -> 5')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Loosen limits' }));
    await waitFor(() => expect(bodies).toHaveLength(2));
    expect(bodies[0]).toEqual({ baseVersion: 3, reason: 'Wider daily room', changes: { maxDailyLossPct: 5 } });
    expect(bodies[1]).toMatchObject({ acknowledgeWeakening: true, changes: { maxDailyLossPct: 5 } });
  });

  it('keeps settings read-only without the admin key', async () => {
    mockApi(BASE);
    renderWithProviders(<RiskManagementPage />, { mock: false });
    fireEvent.click(await screen.findByRole('tab', { name: 'Settings' }));
    expect(await screen.findByLabelText(/Daily loss limit/)).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Save new version' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Activate lock' })).toBeDisabled();
  });

  it('filters the assessment history on the backend', async () => {
    const params: Record<string, unknown>[] = [];
    const row = {
      assessmentId: 'h1', tradeIdeaId: null, instrument: 'EURUSD', direction: 'BUY', strategy: null, decision: 'BLOCK', state: 'active', recommendedVolume: 0.45,
      finalVolume: null, monetaryRisk: null, riskPct: null, riskReward: null, configVersion: 3, createdAt: '2026-11-04T12:00:00+00:00', expiresAt: 'x', source: 'api',
      ruleViolations: ['R030'], warnings: [], errors: [],
    };
    mockApi({ ...BASE, 'GET /risk/history': (c: Cfg) => (params.push({ ...c.params }), { status: 200, data: { total: 1, limit: 20, offset: 0, items: [row] } }) });
    renderWithProviders(<RiskManagementPage />, { mock: false });
    fireEvent.click(await screen.findByRole('tab', { name: 'History' }));
    expect(await screen.findByText('R030')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Decision'), { target: { value: 'BLOCK' } });
    await waitFor(() => expect(params.at(-1)).toEqual({ decision: 'BLOCK', limit: 20, offset: 0 }));
  });
});
