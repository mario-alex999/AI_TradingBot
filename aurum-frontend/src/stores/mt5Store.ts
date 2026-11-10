import { create } from 'zustand';

/** Whether the "Connect your MT5 account" pop-up is showing. Opened right after a server sign-in, and from the top bar. */
interface Mt5UiState {
  promptOpen: boolean;
  setPromptOpen: (open: boolean) => void;
}

export const useMt5Store = create<Mt5UiState>()((set) => ({
  promptOpen: false,
  setPromptOpen: (promptOpen) => set({ promptOpen }),
}));
