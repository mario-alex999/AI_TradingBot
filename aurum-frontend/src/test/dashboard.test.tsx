import { describe, expect, it } from 'vitest';
import { screen, within } from '@testing-library/react';
import DashboardPage from '@/pages/DashboardPage';
import MarketOverviewPage from '@/pages/MarketOverviewPage';
import { renderWithProviders } from './render';
import { mockApi } from './mockApi';

describe('dashboard', () => {
  it('shows a grey dash and awaiting-connection for account figures (never invented)', async () => {
    renderWithProviders(<DashboardPage />);
    const summary = screen.getByRole('region', { name: /account summary/i });
    expect(await within(summary).findAllByText('Awaiting connection')).toHaveLength(6);
    expect(within(summary).getAllByText('—')).toHaveLength(6);
    expect(within(summary).getByLabelText('Account balance: not available')).toBeInTheDocument();
    expect(within(summary).queryByText(/\$\d/)).not.toBeInTheDocument();
  });

  it('labels mock market cards as demo data', async () => {
    renderWithProviders(<DashboardPage />);
    expect(await screen.findByRole('link', { name: 'XAUUSD' })).toBeInTheDocument();
    expect(screen.getAllByText('Demo feed')).toHaveLength(4);
    expect(screen.getAllByText(/demo data/i).length).toBeGreaterThan(0);
  });

  it('shows the analysis-not-yet-available state for intelligence', () => {
    renderWithProviders(<DashboardPage />);
    expect(screen.getAllByText('Analysis not yet available')).toHaveLength(6);
  });

  it('renders all three trading sessions with a timezone selector', () => {
    renderWithProviders(<DashboardPage />);
    for (const s of ['Asian Session', 'London Session', 'New York Session']) expect(screen.getAllByText(s).length).toBeGreaterThan(0);
    expect(screen.getByLabelText('Display timezone')).toHaveValue('UTC');
  });
});

describe('API states', () => {
  it('shows a loading skeleton, then a disconnected-backend error when live data fails', async () => {
    renderWithProviders(<MarketOverviewPage />, { mock: false });
    expect(screen.getAllByRole('status', { name: /loading/i }).length).toBeGreaterThan(0);
    expect(await screen.findByText(/backend disconnected/i)).toBeInTheDocument();
  });

  it('renders live data labelled as backend data', async () => {
    mockApi({
      'GET /health': { status: 'ok', mt5: 'connected', database: 'connected' },
      'GET /market/instruments': [{ symbol: 'EURUSD', name: 'Euro / US Dollar', assetClass: 'forex', enabled: true }],
      'GET /market/quotes': [
        { symbol: 'EURUSD', bid: 1.08123, ask: 1.08131, spread: 8, dailyChangePct: 0.12, dailyHigh: 1.083, dailyLow: 1.079, volume: 1000, status: 'open', digits: 5, updatedAt: '2026-11-02T10:00:00Z' },
      ],
    });
    renderWithProviders(<MarketOverviewPage />, { mock: false });
    expect(await screen.findByText('1.08123')).toBeInTheDocument();
    expect(screen.getByText(/^backend$/i)).toBeInTheDocument();
    expect(screen.queryByText(/demo data/i)).not.toBeInTheDocument();
  });
});
