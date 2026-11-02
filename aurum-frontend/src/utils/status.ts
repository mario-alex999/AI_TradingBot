import type { AgentStatus, ConnectionState } from '@/types/system';

export type Status = ConnectionState | AgentStatus;

export const STATUS_META: Record<Status, { label: string; dot: string; pulse?: boolean }> = {
  connected: { label: 'Connected', dot: 'bg-up' },
  disconnected: { label: 'Disconnected', dot: 'bg-down' },
  collecting: { label: 'Collecting', dot: 'bg-info', pulse: true },
  warning: { label: 'Warning', dot: 'bg-warn' },
  failed: { label: 'Failed', dot: 'bg-down' },
  unknown: { label: 'Unknown', dot: 'bg-neutral' },
  idle: { label: 'Idle', dot: 'bg-neutral' },
  running: { label: 'Running', dot: 'bg-info', pulse: true },
  completed: { label: 'Completed', dot: 'bg-up' },
  disabled: { label: 'Disabled', dot: 'bg-line' },
};

export function statusLabel(s: Status): string {
  return STATUS_META[s].label;
}

