import { useCallback, useEffect } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { ApiError } from '@/services/api';
import { logout, me } from '@/services/authService';
import { useAuthStore } from '@/stores/authStore';
import { useMt5Store } from '@/stores/mt5Store';

/**
 * After a page reload a server session still has its httpOnly cookie, but the in-memory CSRF token is gone. This asks
 * /auth/me for it once. If the session has ended (idle timeout, sign-out elsewhere, disabled account) the profile is
 * cleared so the app returns to the sign-in screen instead of failing on every action.
 */
export function useSessionBootstrap(): void {
  const mode = useAuthStore((s) => s.user?.mode);
  const userId = useAuthStore((s) => s.user?.id);
  useEffect(() => {
    if (mode !== 'server') return;
    let cancelled = false;
    me()
      .then((r) => {
        if (!cancelled) useAuthStore.getState().signInServer(r.user, r.csrfToken);
      })
      .catch((e: unknown) => {
        if (!cancelled && e instanceof ApiError && e.status === 401) useAuthStore.getState().signOut();
      });
    return () => {
      cancelled = true;
    };
  }, [mode, userId]);
}

/** Signs out on the server (ending the session everywhere this cookie is used), then locally. Never blocks sign-out. */
export function useSignOut(): () => Promise<void> {
  const qc = useQueryClient();
  return useCallback(async () => {
    const user = useAuthStore.getState().user;
    if (user?.mode === 'server') {
      try {
        await logout();
      } catch {
        // backend unreachable: the cookie still expires on its own; the local profile is cleared regardless
      }
    }
    useAuthStore.getState().signOut();
    qc.removeQueries({ queryKey: ['approvals'] });
    qc.removeQueries({ queryKey: ['management'] });
    // The backend also logs this person's MT5 terminal out on sign-out.
    qc.removeQueries({ queryKey: ['mt5-connection'] });
    useMt5Store.getState().setPromptOpen(false);
  }, [qc]);
}
