import { Link2, Unlink } from 'lucide-react';
import { StatusDot } from '@/components/common/StatusIndicator';
import { useCanLinkMt5, useMt5Connection, useMt5Disconnect } from '@/hooks/useMt5Link';
import { useMt5Store } from '@/stores/mt5Store';
import type { ConnectionState } from '@/types/system';
import type { Mt5LinkStatus } from '@/services/mt5Service';

const DOT: Record<Mt5LinkStatus, ConnectionState> = {
  CONNECTING: 'collecting',
  CONNECTED: 'connected',
  DISCONNECTING: 'warning',
  DISCONNECTED: 'unknown',
  FAILED: 'failed',
  LOST: 'failed',
};
const WORD: Record<Mt5LinkStatus, string> = {
  CONNECTING: 'Connecting',
  CONNECTED: 'Connected',
  DISCONNECTING: 'Disconnecting',
  DISCONNECTED: 'Not linked',
  FAILED: 'Failed',
  LOST: 'Lost',
};

/** The signed-in person's own MT5 link, in the top bar: status, and a way to link or unlink. */
export function Mt5LinkChip() {
  const can = useCanLinkMt5();
  const conn = useMt5Connection();
  const disconnect = useMt5Disconnect();
  const setPromptOpen = useMt5Store((s) => s.setPromptOpen);
  if (!can || conn.isError) return null;
  const link = conn.data?.connection ?? null;
  const live = !!link && (link.status === 'CONNECTED' || link.status === 'CONNECTING');
  const status = link?.status ?? 'DISCONNECTED';
  const title = link?.errorMessage ?? (link ? `Account ${link.login} on ${link.server}` : 'No MT5 account linked');
  return (
    <span className="inline-flex items-center gap-1.5 text-xs" data-testid="status-my-mt5" title={title}>
      <StatusDot status={DOT[status]} />
      <span className="text-neutral">My MT5</span>
      <span className="text-ink-muted">
        {link && live ? <span className="num">{link.login}</span> : null} {WORD[status]}
      </span>
      {live ? (
        <button type="button" className="ml-0.5 rounded p-0.5 text-ink-muted hover:bg-base-card hover:text-ink" aria-label="Disconnect my MT5 account"
          title="Disconnect" disabled={disconnect.isPending} onClick={() => disconnect.mutate()}>
          <Unlink className="h-3.5 w-3.5" />
        </button>
      ) : (
        <button type="button" className="ml-0.5 rounded p-0.5 text-ink-muted hover:bg-base-card hover:text-ink" aria-label="Connect my MT5 account"
          title="Connect" onClick={() => setPromptOpen(true)}>
          <Link2 className="h-3.5 w-3.5" />
        </button>
      )}
    </span>
  );
}
