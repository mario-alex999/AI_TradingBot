import type { ReactElement } from 'react';
import { render } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useAuthStore } from '@/stores/authStore';
import { useSettingsStore } from '@/stores/settingsStore';
import { useUiStore } from '@/stores/uiStore';
import type { Role } from '@/types/auth';

interface Options {
  route?: string;
  role?: Role | null;
  mock?: boolean;
}

export function renderWithProviders(ui: ReactElement, { route = '/', role = 'administrator', mock = true }: Options = {}) {
  useAuthStore.setState({ user: role ? { id: 't', name: 'Test User', email: 't@test', role } : null });
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
