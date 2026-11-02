import { describe, expect, it } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import ChartsPage from '@/pages/ChartsPage';
import { useChartStore } from '@/stores/chartStore';
import { renderWithProviders } from './render';
import { createdCharts } from './lightweightChartsMock';

describe('charts', () => {
  it('renders a candlestick chart from candle data with a demo watermark', async () => {
    useChartStore.setState({ instrument: 'EURUSD', timeframe: 'H1' });
    renderWithProviders(<ChartsPage />);
    expect(await screen.findByTestId('candlestick-chart')).toBeInTheDocument();
    expect(screen.getAllByText(/demo data/i).length).toBeGreaterThan(0);
    await waitFor(() => expect(createdCharts.at(-1)!.candleData.length).toBeGreaterThan(100));
  });

  it('switches timeframe and instrument', async () => {
    const user = userEvent.setup();
    useChartStore.setState({ instrument: 'EURUSD', timeframe: 'H1' });
    renderWithProviders(<ChartsPage />);
    await screen.findByTestId('candlestick-chart');
    await user.click(screen.getByRole('radio', { name: 'H4' }));
    expect(useChartStore.getState().timeframe).toBe('H4');
    expect(await screen.findByText(/EURUSD · H4/)).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText('Instrument'), 'XAUUSD');
    expect(useChartStore.getState().instrument).toBe('XAUUSD');
    expect(await screen.findByText(/XAUUSD · H4/)).toBeInTheDocument();
  });

  it('only draws indicator panes when enabled', async () => {
    const user = userEvent.setup();
    useChartStore.setState({ instrument: 'EURUSD', timeframe: 'H1' });
    renderWithProviders(<ChartsPage />);
    await screen.findByTestId('candlestick-chart');
    expect(screen.queryByTestId('pane-rsi')).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /indicators/i }));
    await user.click(screen.getByRole('switch', { name: 'RSI' }));
    expect(await screen.findByTestId('pane-rsi')).toBeInTheDocument();
  });

  it('disables structure overlays until the backend supplies them', async () => {
    renderWithProviders(<ChartsPage />);
    expect(await screen.findByRole('switch', { name: 'Order blocks' })).toBeDisabled();
    expect(screen.getAllByText('Awaiting analysis backend').length).toBe(11);
  });
});
