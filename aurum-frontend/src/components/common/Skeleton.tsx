import { cn } from '@/utils/cn';

export function Skeleton({ className }: { className?: string }) {
  return (
    // A flat bar at the value's real size; no shimmer, so nothing shifts when data lands.
    <div className={cn('rounded bg-base-elevated', className)} aria-hidden />
  );
}

export function SkeletonRows({ rows = 4 }: { rows?: number }) {
  return (
    <div className="space-y-2.5" role="status" aria-label="Loading">
      {Array.from({ length: rows }, (_, i) => (
        <Skeleton key={i} className="h-5" />
      ))}
    </div>
  );
}
