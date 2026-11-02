import type { ReactNode } from 'react';
import type { UseQueryResult } from '@tanstack/react-query';
import { ErrorState } from './ErrorState';
import { EmptyState } from './EmptyState';
import { SkeletonRows } from './Skeleton';

interface Props<T> {
  query: UseQueryResult<T>;
  isEmpty?: (data: T) => boolean;
  empty?: ReactNode;
  loading?: ReactNode;
  compact?: boolean;
  children: (data: T) => ReactNode;
}

/** Standard loading / error / empty / data branching for a query. */
export function QueryState<T>({ query, isEmpty, empty, loading, compact, children }: Props<T>) {
  if (query.isPending) return <>{loading ?? <SkeletonRows />}</>;
  if (query.isError) return <ErrorState error={query.error} onRetry={() => void query.refetch()} compact={compact} />;
  if (isEmpty?.(query.data)) return <>{empty ?? <EmptyState title="No data" compact={compact} />}</>;
  return <>{children(query.data)}</>;
}
