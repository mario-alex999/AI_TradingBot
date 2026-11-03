import { Bell, Database, Menu, Search } from 'lucide-react';
import { Link } from 'react-router-dom';
import { useClock } from '@/hooks/useClock';
import { useHealth, useNotifications } from '@/hooks/queries';
import { useSettingsStore } from '@/stores/settingsStore';
import { useNotificationStore } from '@/stores/notificationStore';
import { useUiStore } from '@/stores/uiStore';
import { useAuthStore } from '@/stores/authStore';
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
  const now = useClock();
  const health = useHealth();
  const mockMode = useSettingsStore((s) => s.mockMode);
  const setMobileNavOpen = useUiStore((s) => s.setMobileNavOpen);
  const setPaletteOpen = useUiStore((s) => s.setPaletteOpen);
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

  const user = useAuthStore((st) => st.user);
  const circle = 'relative flex h-9 w-9 items-center justify-center rounded-full border border-line-control text-ink hover:bg-base-card';

  return (
    <header className="sticky top-0 z-20 bg-base px-4 pt-4 lg:px-8 lg:pt-5">
      <div className="flex flex-wrap items-center gap-x-5 gap-y-3 border-b border-line-control pb-4">
        <button
          type="button"
          className="-ml-1 flex h-9 w-9 items-center justify-center rounded-full text-ink-muted hover:bg-base-card lg:hidden"
          aria-label="Open navigation"
          onClick={() => setMobileNavOpen(true)}
        >
          <Menu className="h-5 w-5" />
        </button>

        <div className="min-w-0 flex-1">
          <p className="truncate text-base font-semibold text-ink sm:text-lg">
            Welcome, {user?.name ?? 'Trader'}!
          </p>
          <div className="mt-0.5 flex flex-wrap items-center gap-x-4 gap-y-1" aria-label="System status">
            <span className="inline-flex items-center gap-1.5 text-xs" data-testid="status-market">
              <StatusDot status={fxOpen ? 'connected' : 'idle'} />
              <span className="text-neutral">FX market</span>
              <span className="text-ink-muted">{fxOpen ? 'Open' : 'Closed'}</span>
            </span>
            <span className="hidden items-center gap-x-4 md:inline-flex">
              <StatusChip label="MT5" status={mt5} detail={backendDown ? 'Backend unreachable' : undefined} />
              <StatusChip label="Database" status={db} />
              <StatusChip label="Collection" status={collection} />
            </span>
          </div>
        </div>

        <div className="flex items-center gap-2 sm:gap-2.5">
          {mockMode && (
            <Link to="/settings?tab=data" className="hidden sm:block" title="Demo data mode is on. Change it in Settings">
              <Badge tone="warn">Demo data</Badge>
            </Link>
          )}
          <span
            className={
              isLive
                ? 'rounded-full bg-down-faint px-3 py-1 text-2xs font-semibold tracking-[0.06em] text-down-text'
                : 'rounded-full bg-gold px-3 py-1 text-2xs font-semibold tracking-[0.06em] text-white'
            }
            data-testid="environment-badge"
          >
            <span className="sm:hidden">{isLive ? 'LIVE' : 'DEMO'}</span>
            <span className="hidden sm:inline">{isLive ? 'LIVE ACCOUNT' : 'DEMO ENVIRONMENT'}</span>
          </span>
          <time className="num hidden text-xs text-ink-muted lg:block" dateTime={new Date(now).toISOString()} aria-label="Current UTC time">
            {formatInZone(now, 'UTC')} <span className="text-neutral">UTC</span>
          </time>
          <button type="button" className={circle} aria-label="Search pages and instruments (Ctrl+K)" title="Search  ·  Ctrl+K" onClick={() => setPaletteOpen(true)}>
            <Search className="h-4 w-4" />
          </button>
          <Link to="/data" className={`${circle} hidden sm:flex`} aria-label="Data Centre" title="Data Centre">
            <Database className="h-4 w-4" />
          </Link>
          <Link to="/notifications" className={circle} aria-label={`Notifications${unread ? `, ${unread} unread` : ''}`}>
            <Bell className="h-4 w-4" />
            {unread > 0 && (
              <span className="num absolute -right-1 -top-1 flex h-4 min-w-4 items-center justify-center rounded-full bg-down px-1 text-[11px] font-semibold text-white">
                {unread}
              </span>
            )}
          </Link>
          <UserMenu />
        </div>
      </div>
    </header>
  );
}
