import { Bell, Menu } from 'lucide-react';
import { Link, useLocation } from 'react-router-dom';
import { titleForPath } from '@/config/navigation';
import { useClock } from '@/hooks/useClock';
import { useHealth, useNotifications } from '@/hooks/queries';
import { useSettingsStore } from '@/stores/settingsStore';
import { useNotificationStore } from '@/stores/notificationStore';
import { useUiStore } from '@/stores/uiStore';
import { StatusDot } from '@/components/common/StatusIndicator';
import { statusLabel } from '@/utils/status';
import { Badge } from '@/components/common/Badge';
import { formatInZone } from '@/utils/format';
import { forexMarketOpen } from '@/utils/sessions';
import type { ConnectionState } from '@/types/system';
import { UserMenu } from './UserMenu';

function StatusChip({ label, status, detail }: { label: string; status: ConnectionState; detail?: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-xs" title={detail} data-testid={`status-${label.toLowerCase()}`}>
      <StatusDot status={status} />
      <span className="text-neutral">{label}</span>
      <span className="text-ink-muted">{statusLabel(status)}</span>
    </span>
  );
}

export function Topbar() {
  const { pathname } = useLocation();
  const now = useClock();
  const health = useHealth();
  const mockMode = useSettingsStore((s) => s.mockMode);
  const setMobileNavOpen = useUiStore((s) => s.setMobileNavOpen);
  const notifications = useNotifications();
  const readIds = useNotificationStore((s) => s.readIds);
  const unread = notifications.data?.data.filter((n) => !n.read && !readIds.includes(n.id)).length ?? 0;

  const backendDown = health.isError;
  const h = health.data;
  const mt5: ConnectionState = backendDown ? 'disconnected' : (h?.mt5 ?? 'unknown');
  const db: ConnectionState = backendDown ? 'disconnected' : (h?.database ?? 'unknown');
  const collection: ConnectionState = backendDown ? 'unknown' : (h?.collection ?? 'unknown');
  const fxOpen = forexMarketOpen(now);
  const isLive = h?.environment === 'live'; // only when the backend explicitly says so

  return (
    <header className="sticky top-0 z-20 flex min-h-12 flex-wrap items-center gap-x-5 gap-y-2 border-b border-line bg-base px-4 py-2 lg:px-6">
      <button
        type="button"
        className="-ml-1 flex h-9 w-9 items-center justify-center rounded-md text-ink-muted hover:bg-base-elevated lg:hidden"
        aria-label="Open navigation"
        onClick={() => setMobileNavOpen(true)}
      >
        <Menu className="h-5 w-5" />
      </button>

      <div className="min-w-0">
        <p className="truncate text-sm font-semibold text-ink">{titleForPath(pathname)}</p>
      </div>

      <div className="hidden flex-wrap items-center gap-x-4 gap-y-1 xl:flex" aria-label="System status">
        <span className="inline-flex items-center gap-1.5 text-xs" data-testid="status-market">
          <StatusDot status={fxOpen ? 'connected' : 'idle'} />
          <span className="text-neutral">FX market</span>
          <span className="text-ink-muted">{fxOpen ? 'Open' : 'Closed'}</span>
        </span>
        <StatusChip label="MT5" status={mt5} detail={backendDown ? 'Backend unreachable' : undefined} />
        <StatusChip label="Database" status={db} />
        <StatusChip label="Collection" status={collection} />
      </div>

      <div className="ml-auto flex items-center gap-2">
        {mockMode && (
          <Link to="/settings?tab=data" className="hidden sm:block" title="Mock mode is on — change in Settings">
            <Badge tone="warn">Demo data</Badge>
          </Link>
        )}
        <span
          className={
            isLive
              ? 'rounded border border-down/60 px-2 py-0.5 text-2xs font-semibold tracking-[0.08em] text-down-text'
              : 'rounded border border-gold/60 px-2 py-0.5 text-2xs font-semibold tracking-[0.08em] text-gold'
          }
          data-testid="environment-badge"
        >
          {isLive ? 'LIVE ACCOUNT' : 'DEMO ENVIRONMENT'}
        </span>
        <time className="num hidden text-xs text-ink-muted md:block" dateTime={new Date(now).toISOString()} aria-label="Current UTC time">
          {formatInZone(now, 'UTC')} <span className="text-neutral">UTC</span>
        </time>
        <Link
          to="/notifications"
          className="relative flex h-9 w-9 items-center justify-center rounded-md text-ink-muted hover:bg-base-elevated hover:text-ink"
          aria-label={`Notifications${unread ? `, ${unread} unread` : ''}`}
        >
          <Bell className="h-4 w-4" />
          {unread > 0 && (
            <span className="num absolute right-0.5 top-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-ink px-1 text-[11px] font-semibold text-[#121215]">
              {unread}
            </span>
          )}
        </Link>
        <UserMenu />
      </div>
    </header>
  );
}
