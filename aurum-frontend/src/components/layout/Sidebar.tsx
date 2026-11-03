import { PanelLeftClose, PanelLeftOpen, ShieldCheck } from 'lucide-react';
import { Link } from 'react-router-dom';
import { Logo } from '@/components/common/Logo';
import { env } from '@/config/env';
import { useUiStore } from '@/stores/uiStore';
import { cn } from '@/utils/cn';
import { NavList } from './NavList';

export function Sidebar() {
  const { sidebarCollapsed: collapsed, toggleSidebar } = useUiStore();
  return (
    <aside
      data-testid="sidebar"
      data-collapsed={collapsed}
      className={cn(
        'sticky top-0 hidden h-screen shrink-0 flex-col bg-base-card transition-[width] duration-200 lg:flex',
        collapsed ? 'w-[72px]' : 'w-64',
      )}
    >
      <div className={cn('flex h-20 items-center', collapsed ? 'justify-center' : 'justify-between px-5')}>
        <Link to="/" aria-label="AURUM AI dashboard">
          <Logo collapsed={collapsed} />
        </Link>
      </div>
      <div className="flex-1 overflow-y-auto px-3 pb-4">
        <NavList collapsed={collapsed} />
      </div>
      <div className="border-t border-line-soft p-3">
        {!collapsed && (
          <div className="mb-1 flex flex-col gap-1 px-3 py-2 text-xs text-neutral">
            <span className="flex items-center gap-2">
              <ShieldCheck className="h-3.5 w-3.5" aria-hidden />
              Read-only · no order execution
            </span>
            <Link to="/settings?tab=about" className="truncate hover:text-ink" data-testid="built-by">
              Built by <span className="font-medium text-ink">{env.builtBy}</span> · v{__APP_VERSION__}
            </Link>
          </div>
        )}
        <button
          type="button"
          onClick={toggleSidebar}
          aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
          aria-expanded={!collapsed}
          className={cn(
            'flex h-9 w-full items-center gap-2 rounded-md px-3 text-xs text-ink-muted hover:bg-base-elevated hover:text-ink',
            collapsed && 'justify-center px-0',
          )}
        >
          {collapsed ? <PanelLeftOpen className="h-4 w-4" /> : <PanelLeftClose className="h-4 w-4" />}
          {!collapsed && 'Collapse'}
        </button>
      </div>
    </aside>
  );
}
