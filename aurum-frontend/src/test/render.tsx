import type { ReactElement } from 'react';
import { render } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useAuthStore } from '@/stores/authStore';
import { useSettingsStore } from '@/stores/settingsStore';
import { useUiStore } from '@/stores/uiStore';
import type { Role, User } from '@/types/auth';

interface Options {
  route?: string;
  role?: Role | null;
  mock?: boolean;
  /** Extra user fields, e.g. a Module 07 server session: { mode: 'server', permissions: [...] }. */
  user?: Partial<User>;
}

export function renderWithProviders(ui: ReactElement, { route = '/', role = 'administrator', mock = true, user }: Options = {}) {
  useAuthStore.setState({ user: role ? { id: 't', name: 'Test User', email: 't@test', role, ...user } : null });
  useSettingsStore.setState({ mockMode: mock });
  useUiStore.setState({ sidebarCollapsed: false, mobileNavOpen: false });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return {
    client,
    ...render(
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={[route]}>{ui}</MemoryRouter>
      </QueryClientProvider>,
    ),
  };
}
