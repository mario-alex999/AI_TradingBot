import '@testing-library/jest-dom/vitest';
import { afterEach, vi } from 'vitest';
import { cleanup, configure } from '@testing-library/react';
import { apiClient } from '@/services/api';
import { offlineAdapter } from './mockApi';

// Pages are route-level React.lazy() chunks. The first import of a page in a test run is transformed on demand
// and takes ~2 s on a cold cache - longer than Testing Library's 1 s default for findBy*/waitFor, which made the
// sidebar-navigation test fail. Give lazy chunks a realistic budget.
configure({ asyncUtilTimeout: 10_000 });

// Canvas-based charting cannot run in jsdom; replace it with a recording stub.
vi.mock('lightweight-charts', () => import('./lightweightChartsMock'));

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
globalThis.ResizeObserver = ResizeObserverStub as unknown as typeof ResizeObserver;

// Every test starts with an unreachable backend unless it installs handlers.
apiClient.defaults.adapter = offlineAdapter;

afterEach(() => {
  cleanup();
  apiClient.defaults.adapter = offlineAdapter;
  localStorage.clear();
  sessionStorage.clear();
});
