import type { ReactNode } from 'react';
import { BarChart3 } from 'lucide-react';
import { Card, CardBody, CardHeader } from '@/components/common/Card';
import { EmptyState } from '@/components/common/EmptyState';

interface Props {
  title: string;
  subtitle?: ReactNode;
  actions?: ReactNode;
  height?: number;
  empty?: boolean;
  emptyTitle?: string;
  emptyDescription?: ReactNode;
  children: ReactNode;
}

/** Card wrapper for Recharts with an honest empty state when no data exists. */
export function ChartCard({ title, subtitle, actions, height = 260, empty, emptyTitle = 'No data yet', emptyDescription, children }: Props) {
  return (
    <Card>
      <CardHeader title={title} subtitle={subtitle} actions={actions} />
      <CardBody>
        {empty ? (
          <div style={{ height }} className="flex items-center justify-center">
            <EmptyState compact icon={<BarChart3 className="h-5 w-5" aria-hidden />} title={emptyTitle} description={emptyDescription} />
          </div>
        ) : (
          <div style={{ height }} className="w-full">
            {children}
          </div>
        )}
      </CardBody>
    </Card>
  );
}
