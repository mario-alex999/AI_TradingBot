import { Bell } from 'lucide-react';
import { Link } from 'react-router-dom';
import { Card, CardBody, CardHeader } from '@/components/common/Card';
import { DataSourceBadge } from '@/components/common/DataSourceBadge';
import { QueryState } from '@/components/common/QueryState';
import { EmptyState } from '@/components/common/EmptyState';
import { useNotifications } from '@/hooks/queries';
import { useNotificationStore } from '@/stores/notificationStore';
import { SeverityIcon } from '@/components/agents/SeverityIcon';
import { relativeTime } from '@/utils/format';

export function RecentAlerts() {
  const q = useNotifications();
  const readIds = useNotificationStore((s) => s.readIds);
  return (
    <Card>
      <CardHeader
        icon={<Bell className="h-4 w-4" />}
        title="Recent alerts"
        actions={
          <>
            <DataSourceBadge source={q.data?.source} />
            <Link to="/notifications" className="text-xs font-medium text-gold hover:underline">
              View all
            </Link>
          </>
        }
      />
      <CardBody>
        <QueryState
          query={q}
          compact
          isEmpty={(d) => d.data.length === 0}
          empty={<EmptyState compact title="No alerts" description="System alerts appear here." />}
        >
          {(d) => (
            <ul className="flex flex-col">
              {d.data.slice(0, 4).map((n) => {
                const read = n.read || readIds.includes(n.id);
                return (
                  <li key={n.id} className="flex gap-3 border-b border-line-soft py-2.5 last:border-0">
                    <SeverityIcon severity={n.severity} />
                    <div className="min-w-0 flex-1">
                      <p className={read ? 'truncate text-xs text-ink-muted' : 'truncate text-xs font-medium text-ink'}>{n.title}</p>
                      <p className="truncate text-2xs text-neutral">{n.message}</p>
                    </div>
                    <span className="num shrink-0 text-2xs text-neutral">{relativeTime(n.timestamp)}</span>
                  </li>
                );
              })}
            </ul>
          )}
        </QueryState>
      </CardBody>
    </Card>
  );
}
