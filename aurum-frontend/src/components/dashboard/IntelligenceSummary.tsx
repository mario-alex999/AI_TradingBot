import { Brain } from 'lucide-react';
import { Card, CardBody, CardHeader } from '@/components/common/Card';
import { FeaturePending } from '@/components/common/FeaturePending';

const ITEMS: [string, string][] = [
  ['Market direction', 'Market Structure Agent'],
  ['Market volatility', 'Session & volatility engine'],
  ['Trend strength', 'Market Structure Agent'],
  ['Support & resistance', 'Analysis backend · /analysis/market-structure'],
  ['Upcoming economic events', 'Economic calendar integration'],
  ['Major market movements', 'Market Data Agent alerts'],
];

export function IntelligenceSummary() {
  return (
    <Card>
      <CardHeader icon={<Brain className="h-4 w-4" />} title="Market intelligence summary" subtitle="Populated by the analysis engines once they are deployed." />
      <CardBody className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
        {ITEMS.map(([title, req]) => (
          <FeaturePending key={title} title={title} requires={req} />
        ))}
      </CardBody>
    </Card>
  );
}
