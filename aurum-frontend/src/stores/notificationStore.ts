import { create } from 'zustand';
import { createJSONStorage, persist } from 'zustand/middleware';

/** Local read-state until the backend exposes PATCH /notifications/{id}. */
interface NotificationState {
  readIds: string[];
  markRead: (ids: string[]) => void;
  markUnread: (id: string) => void;
}

export const useNotificationStore = create<NotificationState>()(
  persist(
    (set) => ({
      readIds: [],
      markRead: (ids) => set((s) => ({ readIds: Array.from(new Set([...s.readIds, ...ids])) })),
      markUnread: (id) => set((s) => ({ readIds: s.readIds.filter((x) => x !== id) })),
    }),
    { name: 'aurum.notifications', storage: createJSONStorage(() => localStorage) },
  ),
);
