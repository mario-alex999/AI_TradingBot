import { AlertTriangle, PlugZap, RefreshCw, Wrench } from 'lucide-react';
import { describeError } from '@/utils/errors';
import { Button } from './Button';
import { EmptyState } from './EmptyState';


export function ErrorState({ error, onRetry, compact }: { error: unknown; onRetry?: () => void; compact?: boolean }) {
  const d = describeError(error);
  const Icon = d.kind === 'network' ? PlugZap : d.kind === 'not_implemented' ? Wrench : AlertTriangle;
  return (
    <EmptyState
      compact={compact}
      icon={<Icon className="h-5 w-5" aria-hidden />}
      title={d.title}
      description={d.description}
      action={
        onRetry && d.kind !== 'not_implemented' ? (
          <Button size="sm" icon={<RefreshCw className="h-3.5 w-3.5" aria-hidden />} onClick={onRetry}>
            Retry
          </Button>
        ) : undefined
      }
    />
  );
}
