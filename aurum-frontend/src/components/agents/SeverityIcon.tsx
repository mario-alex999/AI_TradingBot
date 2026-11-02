import { AlertOctagon, Info, TriangleAlert } from 'lucide-react';
import type { Severity } from '@/types/system';

export function SeverityIcon({ severity }: { severity: Severity }) {
  if (severity === 'critical') return <AlertOctagon className="mt-0.5 h-4 w-4 shrink-0 text-down-text" aria-label="Critical" />;
  if (severity === 'warning') return <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0 text-warn" aria-label="Warning" />;
  return <Info className="mt-0.5 h-4 w-4 shrink-0 text-info" aria-label="Info" />;
}
