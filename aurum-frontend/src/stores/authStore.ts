import { create } from 'zustand';
import { createJSONStorage, persist } from 'zustand/middleware';
import type { Role, User } from '@/types/auth';

/**
 * Authentication-ready store. In this DEMO build "signing in" only selects a
 * local demo profile — no password is sent or stored. When the backend adds
 * auth, it should issue an httpOnly session cookie; the frontend keeps only
 * the non-sensitive profile below (in sessionStorage, cleared on tab close).
 */
interface AuthState {
  user: User | null;
  signInDemo: (name: string, role: Role) => void;
  signOut: () => void;
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      user: null,
      signInDemo: (name, role) =>
        set({ user: { id: `demo-${role}`, name: name.trim() || 'Demo User', email: `${role}@demo.aurum.local`, role } }),
      signOut: () => set({ user: null }),
    }),
    { name: 'aurum.session', storage: createJSONStorage(() => sessionStorage) },
  ),
);
