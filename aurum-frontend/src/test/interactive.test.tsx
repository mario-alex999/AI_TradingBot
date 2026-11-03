import { describe, expect, it } from 'vitest';
import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import App from '@/App';
import DashboardPage from '@/pages/DashboardPage';
import MarketOverviewPage from '@/pages/MarketOverviewPage';
import { tickQuote } from '@/hooks/useDemoTicker';
import { useWatchlistStore } from '@/stores/watchlistStore';
import type { Quote } from '@/types/market';
import { renderWithProviders } from './render';

const quote: Quote = {
  symbol: 'EURUSD', bid: 1.1, ask: 1.10008, spread: 8, dailyChangePct: 0, dailyHigh: 1.1, dailyLow: 1.1,
  volume: 1, status: 'unknown', digits: 5, updatedAt: '2026-01-01T00:00:00Z',
};

describe('demo ticker', () => {
  it('keeps the spread, widens the day range and recomputes the change consistently', () => {
    const up = tickQuote(quote, () => 1);
    expect(up.bid).toBeGreaterThan(quote.bid);
    expect(Math.round((up.ask - up.bid) * 1e5)).toBe(8);
    expect(up.dailyHigh).toBe(up.bid);
    expect(up.dailyChangePct).toBeGreaterThan(0);
    const down = tickQuote(quote, () => 0);
    expect(down.dailyLow).toBe(down.bid);
    expect(down.dailyChangePct).toBeLessThan(0);
  });
});

describe('quick search', () => {
  it('opens with Ctrl+K, filters, and navigates with the keyboard', async () => {
    const user = userEvent.setup();
    renderWithProviders(<App />);
    await screen.findByTestId('sidebar');
    await user.keyboard('{Control>}k{/Control}');
    const input = await screen.findByRole('combobox', { name: /search pages and instruments/i });
    await user.type(input, 'data centre');
    expect(screen.getByRole('option', { name: /data centre/i })).toHaveAttribute('aria-selected', 'true');
    await user.keyboard('{Enter}');
    expect(await screen.findByRole('heading', { name: 'Data Centre', level: 1 })).toBeInTheDocument();
    expect(screen.queryByRole('dialog', { name: /quick search/i })).not.toBeInTheDocument();
  });

  it('opens from the header search button and finds instruments', async () => {
    const user = userEvent.setup();
    renderWithProviders(<App />);
    await user.click(await screen.findByRole('button', { name: /search pages and instruments/i }));
    await user.type(screen.getByRole('combobox', { name: /search pages and instruments/i }), 'gold');
    expect(screen.getByRole('option', { name: /XAUUSD/ })).toBeInTheDocument();
  });
});

describe('favourites', () => {
  it('stars an instrument and filters Market Overview to favourites', async () => {
    useWatchlistStore.setState({ favourites: [] });
    const user = userEvent.setup();
    renderWithProviders(<MarketOverviewPage />);
    await screen.findByText('Euro / US Dollar');
    await user.click(screen.getByRole('radio', { name: /favourites/i }));
    expect(await screen.findByText('No favourites yet')).toBeInTheDocument();
    await user.click(screen.getByRole('radio', { name: /all instruments/i }));
    await user.click(screen.getByRole('button', { name: 'Add GBPUSD to favourites' }));
    expect(screen.getByRole('button', { name: 'Remove GBPUSD from favourites' })).toHaveAttribute('aria-pressed', 'true');
    await user.click(screen.getByRole('radio', { name: /favourites \(1\)/i }));
    const table = screen.getByRole('table');
    expect(within(table).getByText('GBPUSD')).toBeInTheDocument();
    expect(within(table).queryByText('EURUSD')).not.toBeInTheDocument();
  });
});

describe('dashboard interactions', () => {
  it('toggles instruments on the performance chart and lists top movers', async () => {
    const user = userEvent.setup();
    renderWithProviders(<DashboardPage />);
    expect(await screen.findByTestId('performance-chart')).toBeInTheDocument();
    const toggle = await screen.findByRole('button', { name: /^US30/ });
    expect(toggle).toHaveAttribute('aria-pressed', 'true');
    await user.click(toggle);
    expect(toggle).toHaveAttribute('aria-pressed', 'false');
    const movers = screen.getByRole('region', { name: /top movers today/i });
    expect(await within(movers).findAllByRole('link')).toHaveLength(4);
  });
});

describe('performance chart with a partial backend', () => {
  it('drops instruments the backend does not have and still draws the rest', async () => {
    const { mockApi } = await import('./mockApi');
    const candles = Array.from({ length: 30 }, (_, i) => ({ time: 1_700_000_000 + i * 3600, open: 1, high: 1.1, low: 0.9, close: 1 + i / 100, volume: 1 }));
    mockApi({
      'GET /health': { status: 'ok' },
      'GET /market/candles': (cfg: { params?: { symbol?: string } }) =>
        cfg.params?.symbol === 'EURUSD' ? { status: 200, data: candles } : { status: 404, data: { detail: 'Unknown instrument' } },
    });
    renderWithProviders(<DashboardPage />, { mock: false });
    expect(await screen.findByText('GBPUSD · no data')).toBeInTheDocument();
    expect(screen.getByText('US30 · no data')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^EURUSD/ })).toHaveTextContent('+29.00%');
    expect(within(screen.getByTestId('performance-chart')).queryByText(/awaiting backend module/i)).not.toBeInTheDocument();
  });
});
