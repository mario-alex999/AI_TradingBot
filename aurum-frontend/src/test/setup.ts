import '@testing-library/jest-dom/vitest';
import { afterEach, vi } from 'vitest';
import { cleanup } from '@testing-library/react';
import { apiClient } from '@/services/api';
import { offlineAdapter } from './mockApi';

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
