import { beforeEach, describe, expect, it } from 'vitest';
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { Mt5LinkChip } from '@/components/mt5/Mt5LinkChip';
import { Mt5Prompt } from '@/components/mt5/Mt5Prompt';
import { useMt5Store } from '@/stores/mt5Store';
import { mockApi } from './mockApi';
import { renderWithProviders } from './render';

type Cfg = { data?: string };

const server = (permissions: string[], accountScope: string[] | null = ['5012345']) => ({
  mode: 'server' as const, username: 'alice', serverRole: 'trader' as const, permissions, accountScope,
});
const LINK = {
  id: 'c1', login: 5012345, server: 'MetaQuotes-Demo', slot: 1, status: 'CONNECTING', company: null, currency: null, leverage: null,
  tradeMode: null, errorCode: null, errorMessage: null, lastSync: null, createdAt: '2026-11-10T09:00:00+00:00', connectedAt: null,
  lastSyncAt: null, disconnectedAt: null, disconnectReason: null,
};

function Shell() {
  return (
    <>
      <Mt5LinkChip />
      <Mt5Prompt />
    </>
  );
}

describe('MT5 account link', () => {
  beforeEach(() => useMt5Store.setState({ promptOpen: false }));

  it('is not offered to the demo workspace or people without the permission', () => {
    renderWithProviders(<Shell />, { mock: false, user: server(['view:dashboard']) });
    expect(screen.queryByTestId('status-my-mt5')).toBeNull();
  });

  it('authorises the pre-filled account, clears the password and shows the result', async () => {
    let body: Record<string, unknown> = {};
    let current: Record<string, unknown> | null = null;
    mockApi({
      'GET /mt5/connection': () => ({ status: 200, data: { connection: current, accountScope: ['5012345'] } }),
      'POST /mt5/connect': (c: Cfg) => {
        body = JSON.parse(c.data ?? '{}');
        current = { ...LINK, status: 'CONNECTED', company: 'MetaQuotes Ltd', tradeMode: 'DEMO', lastSyncAt: '2026-11-10T09:00:05+00:00' };
        return { status: 200, data: { connection: { ...LINK } } };
      },
    });
    renderWithProviders(<Shell />, { mock: false, user: server(['mt5:link']) });
    expect(await screen.findByText('Not linked')).toBeInTheDocument();
    act(() => useMt5Store.getState().setPromptOpen(true));
    const dialog = await screen.findByRole('dialog', { name: 'Connect your MT5 account' });
    const account = within(dialog).getByLabelText(/Account number/) as HTMLInputElement;
    await waitFor(() => expect(account.value).toBe('5012345'));
    expect(account.readOnly).toBe(true);
    const submit = within(dialog).getByRole('button', { name: 'Authorise' });
    expect(submit).toBeDisabled();
    fireEvent.change(within(dialog).getByLabelText(/Broker server/), { target: { value: 'MetaQuotes-Demo' } });
    const pw = within(dialog).getByLabelText(/MT5 password/) as HTMLInputElement;
    fireEvent.change(pw, { target: { value: 'inv3stor!' } });
    fireEvent.click(submit);
    await waitFor(() => expect(body).toEqual({ login: '5012345', password: 'inv3stor!', server: 'MetaQuotes-Demo' }));
    expect(await within(dialog).findByText(/Logging in to account/)).toBeInTheDocument();
    expect(await within(dialog).findByText(/Your account has synced/, {}, { timeout: 4000 })).toBeInTheDocument();
    expect(document.body.innerHTML).not.toContain('inv3stor!');
  });

  it('shows the server refusal (all slots busy) in the pop-up', async () => {
    mockApi({
      'GET /mt5/connection': { connection: null, accountScope: null },
      'POST /mt5/connect': () => ({
        status: 503,
        data: { error: { code: 'ALL_SLOTS_BUSY', message: 'All 2 MT5 terminal slot(s) are in use by other people.' }, detail: 'All 2 MT5 terminal slot(s) are in use by other people.' },
      }),
    });
    renderWithProviders(<Shell />, { mock: false, user: server(['mt5:link'], null) });
    act(() => useMt5Store.getState().setPromptOpen(true));
    const dialog = await screen.findByRole('dialog', { name: 'Connect your MT5 account' });
    fireEvent.change(within(dialog).getByLabelText(/Account number/), { target: { value: '777' } });
    fireEvent.change(within(dialog).getByLabelText(/Broker server/), { target: { value: 'Broker-Demo' } });
    fireEvent.change(within(dialog).getByLabelText(/MT5 password/), { target: { value: 'x' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Authorise' }));
    expect(await within(dialog).findByText(/slot\(s\) are in use/)).toBeInTheDocument();
  });

  it('shows a linked account in the top bar and disconnects it', async () => {
    let disconnected = false;
    mockApi({
      'GET /mt5/connection': () => ({ status: 200, data: { connection: { ...LINK, status: disconnected ? 'DISCONNECTING' : 'CONNECTED' } } }),
      'POST /mt5/disconnect': () => {
        disconnected = true;
        return { status: 200, data: { connection: { ...LINK, status: 'DISCONNECTING' } } };
      },
    });
    renderWithProviders(<Shell />, { mock: false, user: server(['mt5:link']) });
    expect(await screen.findByText('Connected')).toBeInTheDocument();
    expect(screen.getByText('5012345')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Disconnect my MT5 account' }));
    expect(await screen.findByText('Disconnecting')).toBeInTheDocument();
  });
});
