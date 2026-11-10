import { useCanLinkMt5 } from '@/hooks/useMt5Link';
import { useMt5Store } from '@/stores/mt5Store';
import { Mt5AuthorizeDialog } from './Mt5AuthorizeDialog';

/** Mounted once in the app layout: shows the authorisation pop-up after sign-in or when asked from the top bar. */
export function Mt5Prompt() {
  const can = useCanLinkMt5();
  const open = useMt5Store((s) => s.promptOpen);
  const setOpen = useMt5Store((s) => s.setPromptOpen);
  if (!can) return null;
  return <Mt5AuthorizeDialog open={open} onClose={() => setOpen(false)} />;
}
