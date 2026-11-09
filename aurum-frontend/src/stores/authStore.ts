import { create } from 'zustand';
import { createJSONStorage, persist } from 'zustand/middleware';
import { setCsrfToken } from '@/services/api';
import type { ServerUser } from '@/services/authService';
import type { Role, User } from '@/types/auth';

/**
 * Who is using the app.
 *
 * - Server sign-in (Module 07): the backend sets an httpOnly session cookie. Only the non-sensitive profile below is kept
 *   (sessionStorage, cleared when the tab closes); the CSRF token lives in memory and is re-fetched from /auth/me after a
 *   reload (see useSessionBootstrap). No password or session token is ever stored by the browser script.
 * - Demo workspace: a local profile with no backend identity. It can browse, but cannot approve or manage trades.
 */
interface AuthState {
  user: User | null;
  signInDemo: (name: string, role: Role) => void;
  signInServer: (user: ServerUser, csrfToken: string) => void;
  signOut: () => void;
}

/** Backend role -> the existing UI role, so the current navigation permissions keep working unchanged. */
export const UI_ROLE: Record<string, Role> = { admin: 'administrator', trader: 'trader', reviewer: 'analyst', observer: 'viewer' };

export function toUser(u: ServerUser): User {
  return {
    id: u.id,
    name: u.displayName || u.username,
    email: '',
    role: UI_ROLE[u.role] ?? 'viewer',
    mode: 'server',
    username: u.username,
    serverRole: u.role,
    permissions: u.permissions,
    accountScope: u.accountScope,
  };
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      user: null,
      signInDemo: (name, role) =>
        set({ user: { id: `demo-${role}`, name: name.trim() || 'Demo User', email: `${role}@demo.aurum.local`, role, mode: 'demo' } }),
      signInServer: (u, csrf) => {
        setCsrfToken(csrf);
        set({ user: toUser(u) });
      },
      signOut: () => {
        setCsrfToken(null);
        set({ user: null });
      },
    }),
    { name: 'aurum.session', storage: createJSONStorage(() => sessionStorage) },
  ),
);

/** True when a Module 07 permission is present on a server session. Demo profiles never have one. */
export function canServer(user: User | null | undefined, permission: string): boolean {
  return !!user && user.mode === 'server' && !!user.permissions?.includes(permission);
}
