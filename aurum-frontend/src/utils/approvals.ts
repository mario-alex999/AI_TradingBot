import type { ApprovalStatus } from '@/types/approvals';

export const STATUS_LABEL: Record<ApprovalStatus, string> = {
  PENDING: 'Pending',
  UNDER_REVIEW: 'Under review',
  APPROVED: 'Approved',
  REJECTED: 'Rejected',
  MODIFICATION_REQUESTED: 'Changes requested',
  EXPIRED: 'Expired',
  CANCELLED: 'Cancelled',
  INVALIDATED: 'Invalidated',
  EXECUTION_PENDING: 'Awaiting execution',
  EXECUTION_SUBMITTED: 'Submitted',
  EXECUTED: 'Executed',
  EXECUTION_FAILED: 'Execution failed',
};

/** The list of reasons the backend gave (e.g. failed approval checks), or the error message. */
export function errorProblems(e: unknown): string[] {
  const details = (e as { details?: unknown } | null)?.details;
  if (Array.isArray(details)) return details.map(String);
  return e instanceof Error ? [e.message] : ['The request failed.'];
}

export function priceDigits(symbol: string): number {
  if (symbol.endsWith('JPY')) return 3;
  if (symbol.startsWith('XAU') || symbol.startsWith('XAG')) return 2;
  if (/^[A-Z]{6}$/.test(symbol)) return 5;
  return 2;
}
