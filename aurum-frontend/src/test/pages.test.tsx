import { describe, expect, it } from 'vitest';
import { screen } from '@testing-library/react';
import AISignalsPage from '@/pages/AISignalsPage';
import TradeManagementPage from '@/pages/TradeManagementPage';
import COTAnalysisPage from '@/pages/COTAnalysisPage';
import BacktestingPage from '@/pages/BacktestingPage';
import AgentCentrePage from '@/pages/AgentCentrePage';
import RiskManagementPage from '@/pages/RiskManagementPage';
import { renderWithProviders } from './render';

describe('secondary pages', () => {
  it('shows an informative empty state instead of invented signals', async () => {
    renderWithProviders(<AISignalsPage />);
    expect(await screen.findByText('No trade candidates yet')).toBeInTheDocument();
    expect(screen.getByText(/not/, { selector: 'strong' })).toBeInTheDocument();
  });

  it('keeps trade management read-only', async () => {
    renderWithProviders(<TradeManagementPage />);
    expect(await screen.findByText('No open positions')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /buy|sell|close position|modify/i })).not.toBeInTheDocument();
  });

  it('never generates a COT interpretation from demo data', async () => {
    renderWithProviders(<COTAnalysisPage />);
    expect(await screen.findByText(/never produced from demo data/i)).toBeInTheDocument();
    expect(screen.getByText(/synthetic, not CFTC/i)).toBeInTheDocument();
  });

  it('distinguishes backtests from live performance and fabricates no results', async () => {
    renderWithProviders(<BacktestingPage />);
    expect(screen.getByRole('button', { name: /run backtest/i })).toBeDisabled();
    expect(screen.getAllByText(/backtest · simulated/i).length).toBeGreaterThan(0);
    expect(await screen.findAllByText(/no data yet/i)).not.toHaveLength(0);
  });

  it('does not report any agent as running without backend confirmation', async () => {
    renderWithProviders(<AgentCentrePage />);
    expect(await screen.findByText('Market Data Agent')).toBeInTheDocument();
    expect(screen.queryByText('Running')).not.toBeInTheDocument();
  });

  it('computes position size in the informational calculator', async () => {
    renderWithProviders(<RiskManagementPage />);
    expect(await screen.findByTestId('lots')).toHaveTextContent('0.50 lots');
  });
});
