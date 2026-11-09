import { useEffect, useRef, useState } from 'react';
import { ChevronDown, LogOut, Settings, User } from 'lucide-react';
import { Link, useNavigate } from 'react-router-dom';
import { ROLE_LABEL } from '@/config/permissions';
import { useAuthStore } from '@/stores/authStore';
import { useSignOut } from '@/hooks/useSession';

export function UserMenu() {
  const user = useAuthStore((s) => s.user);
  const signOut = useSignOut();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => !ref.current?.contains(e.target as Node) && setOpen(false);
    const esc = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false);
    document.addEventListener('mousedown', close);
    document.addEventListener('keydown', esc);
    return () => {
      document.removeEventListener('mousedown', close);
      document.removeEventListener('keydown', esc);
    };
  }, [open]);

  if (!user) return null;
  const initials = user.name
    .split(/\s+/)
    .map((p) => p[0])
    .join('')
    .slice(0, 2)
    .toUpperCase();

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label="User menu"
        onClick={() => setOpen((o) => !o)}
        className="flex h-10 items-center gap-2 rounded-full p-0.5 hover:bg-base-card"
      >
        <span className="flex h-9 w-9 items-center justify-center rounded-full bg-gold-faint text-xs font-semibold text-gold">
          {initials}
        </span>
        <span className="hidden text-left 2xl:block">
          <span className="block text-xs font-medium leading-tight text-ink">{user.name}</span>
          <span className="block text-2xs leading-tight text-neutral">{ROLE_LABEL[user.role]}</span>
        </span>
        <ChevronDown className="hidden h-3.5 w-3.5 text-neutral 2xl:block" aria-hidden />
      </button>
      {open && (
        <div role="menu" className="absolute right-0 top-11 z-30 w-56 animate-fade-in rounded-xl bg-base-card p-1 shadow-float">
          <div className="border-b border-line-soft px-3 py-2">
            <p className="text-sm font-medium text-ink">{user.name}</p>
            <p className="text-xs text-neutral">{user.email}</p>
          </div>
          <Link role="menuitem" to="/settings?tab=security" onClick={() => setOpen(false)} className="flex items-center gap-2 rounded px-3 py-2 text-sm text-ink-muted hover:bg-base-card hover:text-ink">
            <User className="h-4 w-4" aria-hidden /> Profile
          </Link>
          <Link role="menuitem" to="/settings" onClick={() => setOpen(false)} className="flex items-center gap-2 rounded px-3 py-2 text-sm text-ink-muted hover:bg-base-card hover:text-ink">
            <Settings className="h-4 w-4" aria-hidden /> Settings
          </Link>
          <button
            role="menuitem"
            type="button"
            onClick={() => {
              void signOut().then(() => navigate('/login'));
            }}
            className="flex w-full items-center gap-2 rounded px-3 py-2 text-sm text-ink-muted hover:bg-base-card hover:text-ink"
          >
            <LogOut className="h-4 w-4" aria-hidden /> Sign out
          </button>
        </div>
      )}
    </div>
  );
}
