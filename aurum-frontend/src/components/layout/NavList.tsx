import { NavLink } from 'react-router-dom';
import { NAV_GROUPS } from '@/config/navigation';
import { useAuthStore } from '@/stores/authStore';
import { hasPermission } from '@/utils/permissions';
import { cn } from '@/utils/cn';

/** Role-aware navigation shared by the desktop sidebar and mobile drawer. */
export function NavList({ collapsed = false, onNavigate }: { collapsed?: boolean; onNavigate?: () => void }) {
  const role = useAuthStore((s) => s.user?.role);
  return (
    <nav aria-label="Primary" className="flex flex-col gap-4">
      {NAV_GROUPS.map((group) => {
        const items = group.items.filter((i) => hasPermission(role, i.permission));
        if (items.length === 0) return null;
        return (
          <div key={group.label}>
            {collapsed ? (
              <div className="mx-3 mb-2 border-t border-line-soft" aria-hidden />
            ) : (
              <p className="eyebrow mb-1.5 px-3">{group.label}</p>
            )}
            <ul className="flex flex-col gap-0.5">
              {items.map((item) => {
                const Icon = item.icon;
                return (
                  <li key={item.path}>
                    <NavLink
                      to={item.path}
                      end={item.path === '/'}
                      onClick={onNavigate}
                      title={collapsed ? item.label : undefined}
                      aria-label={collapsed ? item.label : undefined}
                      className={({ isActive }) =>
                        cn(
                          'group relative flex h-9 items-center gap-3 rounded-md px-3 text-[13px] transition-colors',
                          collapsed && 'justify-center px-0',
                          isActive
                            ? 'bg-gold-faint font-medium text-ink'
                            : 'text-ink-muted hover:bg-base-elevated hover:text-ink',
                        )
                      }
                    >
                      {({ isActive }) => (
                        <>
                          {isActive && <span className="absolute inset-y-2 left-0 w-0.5 rounded-r bg-gold" aria-hidden />}
                          <Icon className={cn('h-4 w-4 shrink-0', isActive ? 'text-gold' : 'text-neutral group-hover:text-ink-muted')} aria-hidden />
                          {!collapsed && <span className="truncate">{item.label}</span>}
                        </>
                      )}
                    </NavLink>
                  </li>
                );
              })}
            </ul>
          </div>
        );
      })}
    </nav>
  );
}
