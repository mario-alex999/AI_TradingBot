import { useMemo, useState } from 'react';
import { CheckCheck } from 'lucide-react';
import { PageHeader } from '@/components/common/PageHeader';
import { Card } from '@/components/common/Card';
import { Select } from '@/components/common/Select';
import { Button } from '@/components/common/Button';
import { Badge } from '@/components/common/Badge';
import { DataSourceBadge } from '@/components/common/DataSourceBadge';
import { QueryState } from '@/components/common/QueryState';
import { EmptyState } from '@/components/common/EmptyState';
import { SegmentedControl } from '@/components/common/SegmentedControl';
import { SeverityIcon } from '@/components/agents/SeverityIcon';
import { useNotifications } from '@/hooks/queries';
import { useNotificationStore } from '@/stores/notificationStore';
import { formatDateTime, relativeTime } from '@/utils/format';
import { cn } from '@/utils/cn';
import { CATEGORY_LABEL } from '@/config/notifications';
import type { NotificationCategory, Severity } from '@/types/system';


export default function NotificationsPage() {
  const q = useNotifications();
  const { readIds, markRead, markUnread } = useNotificationStore();
  const [readFilter, setReadFilter] = useState<'all' | 'unread' | 'read'>('all');
  const [severity, setSeverity] = useState<Severity | ''>('');
  const [category, setCategory] = useState<NotificationCategory | ''>('');

  const items = useMemo(
    () =>
      (q.data?.data ?? [])
        .map((n) => ({ ...n, read: n.read || readIds.includes(n.id) }))
        .filter(
          (n) =>
            (readFilter === 'all' || (readFilter === 'read') === n.read) &&
            (!severity || n.severity === severity) &&
            (!category || n.category === category),
        )
        .sort((a, b) => b.timestamp.localeCompare(a.timestamp)),
    [q.data, readIds, readFilter, severity, category],
  );
  const unreadIds = items.filter((n) => !n.read).map((n) => n.id);

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        eyebrow="System"
        title="Notifications"
        description="System, data and trading alerts."
        actions={
          <>
            <DataSourceBadge source={q.data?.source} fetchedAt={q.data?.fetchedAt} />
            <Button size="sm" icon={<CheckCheck className="h-3.5 w-3.5" aria-hidden />} disabled={unreadIds.length === 0} onClick={() => markRead(unreadIds)}>
              Mark all as read
            </Button>
          </>
        }
      />
      <div className="flex flex-wrap items-end gap-3">
        <SegmentedControl
          label="Read status"
          value={readFilter}
          onChange={setReadFilter}
          options={[
            { value: 'all', label: 'All' },
            { value: 'unread', label: 'Unread' },
            { value: 'read', label: 'Read' },
          ]}
        />
        <Select
          label="Severity"
          hideLabel
          value={severity}
          onChange={(v) => setSeverity(v as Severity | '')}
          options={[
            { value: '', label: 'All severities' },
            { value: 'critical', label: 'Critical' },
            { value: 'warning', label: 'Warning' },
            { value: 'info', label: 'Info' },
          ]}
          className="w-40"
        />
        <Select
          label="Category"
          hideLabel
          value={category}
          onChange={(v) => setCategory(v as NotificationCategory | '')}
          options={[{ value: '', label: 'All categories' }, ...Object.entries(CATEGORY_LABEL).map(([value, label]) => ({ value, label }))]}
          className="w-56"
        />
      </div>
      <Card>
        <QueryState query={q} isEmpty={() => items.length === 0} empty={<EmptyState title="No notifications" description="Nothing matches the current filters." />}>
          {() => (
            <ul>
              {items.map((n) => (
                <li key={n.id} className={cn('flex gap-3 border-b border-line-soft px-4 py-3 last:border-0', !n.read && 'bg-base-raised')}>
                  <SeverityIcon severity={n.severity} />
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      {!n.read && <span className="h-1.5 w-1.5 rounded-full bg-ink" aria-label="Unread" />}
                      <p className={n.read ? 'text-sm text-ink-muted' : 'text-sm font-medium text-ink'}>{n.title}</p>
                      <Badge>{CATEGORY_LABEL[n.category]}</Badge>
                    </div>
                    <p className="mt-0.5 text-xs text-neutral">{n.message}</p>
                    <p className="num mt-1 text-2xs text-neutral" title={`${formatDateTime(n.timestamp)} UTC`}>
                      {relativeTime(n.timestamp)}
                    </p>
                  </div>
                  <Button size="sm" variant="ghost" onClick={() => (n.read ? markUnread(n.id) : markRead([n.id]))}>
                    {n.read ? 'Mark unread' : 'Mark as read'}
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </QueryState>
      </Card>
    </div>
  );
}
