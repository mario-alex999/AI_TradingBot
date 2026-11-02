import type { Tone } from '@/components/common/Badge';
import type { SignalStatus } from '@/types/trading';

export const SIGNAL_STATUS: Record<SignalStatus, { label: string; tone: Tone }> = {
  new: { label: 'New', tone: 'gold' },
  under_review: { label: 'Under review', tone: 'info' },
  approved: { label: 'Approved', tone: 'up' },
  rejected: { label: 'Rejected', tone: 'down' },
  expired: { label: 'Expired', tone: 'neutral' },
  executed: { label: 'Executed', tone: 'up' },
};
