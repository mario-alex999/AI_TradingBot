import { QueryClient } from '@tanstack/react-query';
import { ApiError } from '@/services/api';

export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        // Retry only transient failures (network, timeout, 5xx), at most twice.
        retry: (count, error) => (error instanceof ApiError ? error.retryable && count < 2 : count < 1),
        retryDelay: (attempt) => Math.min(1000 * 2 ** attempt, 8000),
        staleTime: 30_000,
        refetchOnWindowFocus: false,
      },
    },
  });
}
