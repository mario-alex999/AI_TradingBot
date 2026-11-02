import { FlaskConical, Radio } from 'lucide-react';
import { Badge } from './Badge';
import type { DataSource } from '@/types/api';
import { relativeTime } from '@/utils/format';

/** Mandatory on every panel that renders fetched data. */
export function DataSourceBadge({ source, fetchedAt }: { source?: DataSource; fetchedAt?: string }) {
  if (!source) return null;
  return source === 'demo' ? (
    <Badge tone="gold">
      <FlaskConical className="h-3 w-3" aria-hidden /> Demo data
    </Badge>
  ) : (
    <span className="inline-flex items-center gap-1.5">
      <Badge tone="up">
        <Radio className="h-3 w-3" aria-hidden /> Backend
      </Badge>
      {fetchedAt && <span className="text-2xs text-neutral">{relativeTime(fetchedAt)}</span>}
    </span>
  );
}
