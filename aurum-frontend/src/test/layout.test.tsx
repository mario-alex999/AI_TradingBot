import { describe, expect, it } from 'vitest';
import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import App from '@/App';
import { renderWithProviders } from './render';

describe('application shell', () => {
  it('redirects unauthenticated users to the login screen', async () => {
    renderWithProviders(<App />, { role: null });
    expect(await screen.findByRole('button', { name: /enter demo workspace/i })).toBeInTheDocument();
  });

  it('signs in to the demo workspace through the validated form', async () => {
    const user = userEvent.setup();
    renderWithProviders(<App />, { role: null });
    const name = await screen.findByLabelText(/display name/i);
    await user.clear(name);
    await user.click(screen.getByRole('button', { name: /enter demo workspace/i }));
    expect(await screen.findByText(/enter at least 2 characters/i)).toBeInTheDocument();
    await user.type(name, 'Ada Trader');
    await user.click(screen.getByRole('button', { name: /enter demo workspace/i }));
    expect(await screen.findByRole('heading', { name: /aurum ai market intelligence/i })).toBeInTheDocument();
  });

  it('shows the demo environment prominently and never claims live trading', async () => {
    renderWithProviders(<App />);
    expect(await screen.findByTestId('environment-badge')).toHaveTextContent('DEMO ENVIRONMENT');
  });

  it('navigates between pages from the sidebar', async () => {
    const user = userEvent.setup();
    renderWithProviders(<App />);
    const sidebar = await screen.findByTestId('sidebar');
    await user.click(within(sidebar).getByRole('link', { name: /data centre/i }));
    expect(await screen.findByRole('heading', { name: 'Data Centre', level: 1 })).toBeInTheDocument();
    await user.click(within(sidebar).getByRole('link', { name: /market overview/i }));
    expect(await screen.findByRole('heading', { name: 'Market Overview', level: 1 })).toBeInTheDocument();
    expect(within(sidebar).getByRole('link', { name: /market overview/i })).toHaveAttribute('aria-current', 'page');
  });

  it('collapses and expands the sidebar', async () => {
    const user = userEvent.setup();
    renderWithProviders(<App />);
    const sidebar = await screen.findByTestId('sidebar');
    expect(within(sidebar).getByText('Charts & Analysis')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /collapse sidebar/i }));
    expect(sidebar).toHaveAttribute('data-collapsed', 'true');
    expect(within(sidebar).queryByText('Charts & Analysis')).not.toBeInTheDocument();
    expect(within(sidebar).getByRole('link', { name: 'Charts & Analysis' })).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /expand sidebar/i }));
    expect(sidebar).toHaveAttribute('data-collapsed', 'false');
  });

  it('opens the mobile navigation drawer', async () => {
    const user = userEvent.setup();
    renderWithProviders(<App />);
    await user.click(await screen.findByRole('button', { name: /open navigation/i }));
    const drawer = screen.getByRole('dialog', { name: /navigation/i });
    expect(within(drawer).getByRole('link', { name: /settings/i })).toBeInTheDocument();
  });

  it('hides trading navigation from viewers and blocks direct access', async () => {
    renderWithProviders(<App />, { role: 'viewer', route: '/signals' });
    const sidebar = await screen.findByTestId('sidebar');
    expect(within(sidebar).queryByRole('link', { name: /ai signals/i })).not.toBeInTheDocument();
    expect(await screen.findByText(/access restricted/i)).toBeInTheDocument();
  });
});
