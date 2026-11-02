import { Sparkles } from 'lucide-react';
import { Card, CardBody, CardHeader } from '@/components/common/Card';
import { Badge } from '@/components/common/Badge';
import type { DataSource } from '@/types/api';

const FIELDS = ['Positioning trend', 'Major weekly changes', 'Historical extremes', 'Divergence from price', 'Potential market implications'];

/**
 * AI interpretation panel. It renders interpretations ONLY when the backend
 * supplies them for live data — never from demo data or missing history.
 */
export function CotInterpretation({ source, hasData }: { source?: DataSource; hasData: boolean }) {
  const reason =
    source === 'demo'
      ? 'Not generated: interpretations are never produced from demo data.'
      : !hasData
        ? 'Not generated: COT data for this contract is missing.'
        : 'Awaiting the COT Intelligence Agent interpretation endpoint.';
  return (
    <Card>
      <CardHeader icon={<Sparkles className="h-4 w-4" />} title="COT interpretation" subtitle="AI-generated summary of positioning. Describes positioning only — not trader profit or loss." actions={<Badge>Not generated</Badge>} />
      <CardBody>
        <p className="mb-3 rounded-md border border-line-soft bg-base-raised px-3 py-2 text-xs text-ink-muted">{reason}</p>
        <dl className="flex flex-col">
          {FIELDS.map((f) => (
            <div key={f} className="flex justify-between gap-4 border-b border-line-soft py-2 text-xs last:border-0">
              <dt className="text-neutral">{f}</dt>
              <dd className="text-ink-muted">--</dd>
            </div>
          ))}
        </dl>
      </CardBody>
    </Card>
  );
}
