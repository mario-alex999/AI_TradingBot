import type { Tone } from '@/components/common/Badge';
import type { RiskDecision, RiskStatus, RuleSeverity } from '@/types/risk';

export const STATUS_LABEL: Record<RiskStatus, string> = {
  WITHIN_LIMITS: 'Within limits',
  WARNING: 'Warning',
  BLOCKED: 'Blocked',
  DATA_UNAVAILABLE: 'Data unavailable',
};

export const STATUS_TONE: Record<RiskStatus, Tone> = {
  WITHIN_LIMITS: 'up',
  WARNING: 'warn',
  BLOCKED: 'down',
  DATA_UNAVAILABLE: 'neutral',
};

export const DECISION_LABEL: Record<RiskDecision, string> = {
  PASS: 'Pass',
  REVIEW: 'Review',
  BLOCK: 'Block',
  ERROR: 'Error',
};

export const DECISION_TONE: Record<RiskDecision, Tone> = { PASS: 'up', REVIEW: 'warn', BLOCK: 'down', ERROR: 'neutral' };

export const DECISION_MEANING: Record<RiskDecision, string> = {
  PASS: 'All risk rules passed. The trade still needs human approval; AURUM AI never places orders.',
  REVIEW: 'No hard rule failed, but warnings must be reviewed before the trade can be approved.',
  BLOCK: 'A hard rule failed. Nothing downstream can override a block.',
  ERROR: 'Required data was missing or invalid, so the engine could not decide. Treated as not approved.',
};

export const SEVERITY_LABEL: Record<RuleSeverity, string> = { HARD_BLOCK: 'Hard block', WARNING: 'Warning', INFORMATIONAL: 'Info' };

/** Share of a limit used, clamped for a progress bar. null when either side is unknown. */
export function usage(value: number | null | undefined, limit: number | null | undefined): number | null {
  if (value == null || limit == null || limit <= 0) return null;
  return Math.max(0, Math.min(1, value / limit));
}

export function usageTone(share: number | null): 'up' | 'warn' | 'down' | 'neutral' {
  if (share == null) return 'neutral';
  if (share >= 1) return 'down';
  if (share >= 0.75) return 'warn';
  return 'up';
}
