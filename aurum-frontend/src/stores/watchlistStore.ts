import { create } from 'zustand';
import { createJSONStorage, persist } from 'zustand/middleware';

/** Starred instruments (a per-browser convenience; not sensitive). */
interface WatchlistState {
  favourites: string[];
  toggle: (symbol: string) => void;
  isFavourite: (symbol: string) => boolean;
}

export const useWatchlistStore = create<WatchlistState>()(
  persist(
    (set, get) => ({
      favourites: ['XAUUSD'],
      toggle: (symbol) =>
        set((s) => ({
          favourites: s.favourites.includes(symbol) ? s.favourites.filter((x) => x !== symbol) : [...s.favourites, symbol],
        })),
      isFavourite: (symbol) => get().favourites.includes(symbol),
    }),
    { name: 'aurum.watchlist', storage: createJSONStorage(() => localStorage) },
  ),
);
