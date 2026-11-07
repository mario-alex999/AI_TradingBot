import { create } from 'zustand';

interface AdminKeyState {
  key: string;
  setKey: (key: string) => void;
  clear: () => void;
}

/**
 * The backend admin key, held in memory only: never persisted, so it is gone on reload
 * and never reaches storage or the bundle. The backend does the authorising.
 */
export const useAdminKeyStore = create<AdminKeyState>()((set) => ({
  key: '',
  setKey: (key) => set({ key: key.trim() }),
  clear: () => set({ key: '' }),
}));
