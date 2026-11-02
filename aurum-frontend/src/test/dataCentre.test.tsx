import { describe, expect, it } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import DataCentrePage from '@/pages/DataCentrePage';
import { renderWithProviders } from './render';
import { mockApi } from './mockApi';
import { demoSpecifications } from '@/mocks/data';

const liveBackend = (capabilities: string[], onCollect?: () => void) =>
  mockApi({
    'GET /health': { status: 'ok', environment: 'demo', mt5: 'connected', database: 'connected', collection: 'connected', capabilities },
    'GET /market/specifications': demoSpecifications(),
    'GET /market/data-quality': { instrumentsMonitored: [], timeframes: [], byTimeframe: [] },
    'GET /market/collection-logs': { items: [], total: 0, page: 1, pageSize: 12 },
    'POST /market/collect': () => {
      onCollect?.();
      return { status: 202, data: { jobId: 'job-1', status: 'queued' } };
    },
  });

describe('Data Centre controls', () => {
  it('disables operational actions in demo-data mode', async () => {
    renderWithProviders(<DataCentrePage />);
    expect(screen.getByRole('button', { name: /start collection/i })).toBeDisabled();
    expect(screen.getByRole('button', { name: /export csv/i })).toBeDisabled();
    expect(await screen.findByTestId('collect-disabled-reason')).toHaveTextContent(/demo-data mode/i);
  });

  it('keeps actions disabled when the backend does not advertise the endpoint', async () => {
    liveBackend([]);
    renderWithProviders(<DataCentrePage />, { mock: false });
    await waitFor(() => expect(screen.getByTestId('collect-disabled-reason')).toHaveTextContent(/endpoint not exposed/i));
  });

  it('respects role permissions even when the endpoint exists', async () => {
    liveBackend(['market.collect']);
    renderWithProviders(<DataCentrePage />, { mock: false, role: 'analyst' });
    await waitFor(() => expect(screen.getByTestId('collect-disabled-reason')).toHaveTextContent(/administrator/i));
  });

  it('starts a collection after confirmation for an administrator', async () => {
    const user = userEvent.setup();
    let calls = 0;
    liveBackend(['market.collect'], () => calls++);
    renderWithProviders(<DataCentrePage />, { mock: false });
    const btn = screen.getByRole('button', { name: /start collection/i });
    await waitFor(() => expect(btn).toBeEnabled());
    await user.click(btn);
    const dialog = await screen.findByRole('dialog');
    await user.click(within(dialog).getByRole('button', { name: /^start collection$/i }));
    await waitFor(() => expect(calls).toBe(1));
  });

  it('paginates and filters demo collection logs', async () => {
    const user = userEvent.setup();
    renderWithProviders(<DataCentrePage />);
    expect(await screen.findByText(/1–12 of 96/)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /next page/i }));
    expect(await screen.findByText(/13–24 of 96/)).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText('Validation filter'), 'failed');
    await waitFor(() => expect(screen.queryByText(/of 96/)).not.toBeInTheDocument());
  });
});
