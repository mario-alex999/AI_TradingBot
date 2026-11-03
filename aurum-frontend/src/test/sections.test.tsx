import { describe, expect, it } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import App from '@/App';
import MarketStructurePage from '@/pages/MarketStructurePage';
import DataCentrePage from '@/pages/DataCentrePage';
import RiskManagementPage from '@/pages/RiskManagementPage';
import { SessionPanel } from '@/components/dashboard/SessionPanel';
import { useDataCentreStore } from '@/stores/dataCentreStore';
import { renderWithProviders } from './render';

describe('keyboard shortcuts and credit', () => {
  it('opens the shortcuts sheet with ? and navigates with G then a letter', async () => {
    const user = userEvent.setup();
    renderWithProviders(<App />);
    await screen.findByTestId('sidebar');
    await user.keyboard('?');
    expect(screen.getByRole('dialog', { name: /keyboard shortcuts/i })).toBeInTheDocument();
    await user.keyboard('{Escape}');
    await user.keyboard('ge');
    expect(await screen.findByRole('heading', { name: 'Data Centre', level: 1 })).toBeInTheDocument();
  });

  it('ignores shortcuts while typing in a field', async () => {
    const user = userEvent.setup();
    renderWithProviders(<App />, { route: '/market' });
    const search = await screen.findByRole('searchbox', { name: /search instruments/i });
    await user.type(search, 'ge?');
    expect(search).toHaveValue('ge?');
    expect(screen.queryByRole('dialog', { name: /keyboard shortcuts/i })).not.toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Market Overview', level: 1 })).toBeInTheDocument();
  });

  it('shows the Built by credit in the sidebar and the About tab', async () => {
    const user = userEvent.setup();
    renderWithProviders(<App />);
    const credit = await screen.findByTestId('built-by');
    expect(credit).toHaveTextContent(/built by/i);
    await user.click(credit);
    expect(await screen.findByTestId('about-built-by')).toBeInTheDocument();
    expect(screen.getByText(/^v\d+\.\d+\.\d+$/)).toBeInTheDocument();
  });
});

describe('Market Structure drill-down', () => {
  it('moves between timeframes with arrow keys and opens a cell in the drawer', async () => {
    const user = userEvent.setup();
    renderWithProviders(<MarketStructurePage />);
    const list = await screen.findByRole('listbox', { name: /timeframes/i });
    const h4 = within(list).getByRole('option', { name: /H4/ });
    expect(h4).toHaveAttribute('aria-selected', 'true');
    h4.focus();
    await user.keyboard('{ArrowDown}');
    expect(within(list).getByRole('option', { name: /H1/ })).toHaveAttribute('aria-selected', 'true');
    await user.click(screen.getByRole('button', { name: 'Break of structure on M15: awaiting analysis' }));
    const drawer = screen.getByRole('dialog', { name: /break of structure · EURUSD M15/i });
    expect(within(drawer).getByText(/never generates these events/i)).toBeInTheDocument();
  });

  it('explains trading terms in info tips', async () => {
    const user = userEvent.setup();
    renderWithProviders(<MarketStructurePage />);
    await user.hover(await screen.findByRole('button', { name: /what is change of character/i }));
    expect(screen.getByRole('tooltip')).toHaveTextContent(/first break against the trend/i);
  });
});

describe('Data Centre drill-down', () => {
  it('filters the logs from a quality tile and opens a log in the drawer', async () => {
    useDataCentreStore.setState({ timeframe: '', status: '' });
    const user = userEvent.setup();
    renderWithProviders(<DataCentrePage />);
    expect(await screen.findByText(/1–12 of 96/)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /collection errors/i }));
    expect(useDataCentreStore.getState().status).toBe('failed');
    await waitFor(() => expect(screen.queryByText(/of 96/)).not.toBeInTheDocument());
    expect(screen.getByText(/filtered · failed/i)).toBeInTheDocument();
    const firstRow = screen.getAllByRole('row', { name: /open log for/i })[0]!;
    await user.click(firstRow);
    const drawer = screen.getByRole('dialog');
    expect(within(drawer).getByText('Records retrieved')).toBeInTheDocument();
    expect(within(drawer).getByText(/terminal not responding/i)).toBeInTheDocument();
    await user.click(within(drawer).getByRole('button', { name: /close details/i }));
    await user.click(screen.getByRole('button', { name: /clear filters/i }));
    expect(await screen.findByText(/1–12 of 96/)).toBeInTheDocument();
  });
});

describe('sessions and risk', () => {
  it('expands a session for its details', async () => {
    const user = userEvent.setup();
    renderWithProviders(<SessionPanel />);
    const toggle = screen.getByRole('button', { name: /london session/i });
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    await user.click(toggle);
    expect(toggle).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByText('Local hours')).toBeInTheDocument();
  });

  it('updates the position size from the risk presets', async () => {
    const user = userEvent.setup();
    renderWithProviders(<RiskManagementPage />);
    expect(await screen.findByTestId('lots')).toHaveTextContent('0.50 lots');
    await user.click(screen.getByRole('button', { name: '2%' }));
    expect(screen.getByTestId('lots')).toHaveTextContent('1.00 lots');
  });
});
