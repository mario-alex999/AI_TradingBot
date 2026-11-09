/** Module 07 shapes, as returned by /api/v1/approvals and /api/v1/trade-management (camelCase). Prices are numbers for
 * display only; anything sent back to confirm an approval is taken from the server's own values. */

export type ApprovalStatus =
  | 'PENDING'
  | 'UNDER_REVIEW'
  | 'APPROVED'
  | 'REJECTED'
  | 'MODIFICATION_REQUESTED'
  | 'EXPIRED'
  | 'CANCELLED'
  | 'INVALIDATED'
  | 'EXECUTION_PENDING'
  | 'EXECUTION_SUBMITTED'
  | 'EXECUTED'
  | 'EXECUTION_FAILED';

export type RiskDecision = 'PASS' | 'REVIEW' | 'BLOCK' | 'ERROR';

export interface RiskWarning {
  ruleId: string;
  name?: string | null;
  message: string;
}

export interface ApprovalRequest {
  approvalId: string;
  ideaId: string;
  ideaVersion: number;
  parentApprovalId: string | null;
  status: ApprovalStatus;
  version: number;
  symbol: string;
  direction: 'BUY' | 'SELL';
  timeframe: string;
  strategy: string;
  entry: number;
  stopLoss: number;
  takeProfit: number;
  positionSize: number | null;
  riskAmount: number | null;
  riskPct: number | null;
  riskReward: number | null;
  aiConfidence: number | null;
  riskDecision: RiskDecision;
  riskAssessmentId: string | null;
  riskAssessedAt: string | null;
  riskConfigVersion: number | null;
  riskWarnings: RiskWarning[];
  evidenceSnapshotId: string | null;
  evidenceHash: string | null;
  analysisVersion: string;
  policyVersion: number;
  accountLogin: string | null;
  assignedUserId: string | null;
  approvalsRequired: number;
  createdAt: string;
  expiresAt: string;
  secondsRemaining: number | null;
  statusChangedAt: string;
  statusReason: string | null;
  decidedAt: string | null;
  canApprove: boolean;
  correlationId: string;
}

export interface ApprovalPage {
  items: ApprovalRequest[];
  total: number;
  page: number;
  pageSize: number;
}

export interface ApprovalDecisionRow {
  decision: string;
  isFinal: boolean;
  username: string;
  role: string;
  method: string;
  decidedAt: string;
  reasonCode: string | null;
  comment: string | null;
  evidenceHash: string | null;
  riskAssessmentId: string | null;
  acknowledgedWarnings: string[] | null;
  recordHash: string;
}

export interface HandoffResult {
  eventId: string;
  status: string;
  brokerTicket: string | null;
  positionId: string | null;
  filledVolume: number | null;
  fillPrice: number | null;
  message: string | null;
  reportedBy: string;
  reportedAt: string;
}

export interface Handoff {
  executionRequestId: string;
  kind: 'trade' | 'management';
  status: string;
  createdAt: string;
  pickedUpAt: string | null;
  payload: Record<string, unknown>;
  results: HandoffResult[];
}

export interface ApprovalDetail {
  request: ApprovalRequest;
  idea: { ideaId: string; source: string; sourceRef: string | null; market: string; status: string; currentVersion: number; expiresAt: string };
  versions: {
    version: number;
    direction: string;
    entry: number;
    stopLoss: number;
    takeProfits: string[];
    requestedVolume: number | null;
    analysisVersion: string;
    createdBy: string;
    reason: string | null;
    createdAt: string;
  }[];
  decisions: ApprovalDecisionRow[];
  riskAssessments: {
    purpose: string;
    assessmentId: string | null;
    decision: string;
    finalVolume: number | null;
    riskPct: number | null;
    riskAmount: number | null;
    configVersion: number | null;
    assessedAt: string;
  }[];
  modifications: { id: string; kinds: string[]; changes: Record<string, string> | null; comment: string | null; status: string; newApprovalId: string | null; approvalId: string; createdAt: string }[];
  handoff: Handoff | null;
  relatedRequests: { approvalId: string; status: ApprovalStatus; ideaVersion: number; createdAt: string }[];
  disclaimer: string;
}

export type EvidenceKind = 'confirmed' | 'ai_interpretation' | 'historical' | 'contradictory' | 'risk_warning' | 'missing';

export interface EvidenceItem {
  kind: EvidenceKind;
  label: string;
  value: unknown;
  source: string | null;
  asOf: string | null;
  /** Present on 'missing' items: why the data is not available. */
  reason?: string;
  [k: string]: unknown;
}

export interface EvidenceSection {
  name: string;
  source: string;
  status: 'available' | 'missing' | 'partial';
  reason: string | null;
  asOf: string | null;
  items: EvidenceItem[];
}

export type EvidenceSectionKey = 'trade' | 'technical' | 'market' | 'institutional' | 'ai' | 'risk';

export interface Evidence {
  snapshotId: string;
  hash: string;
  createdAt: string;
  completeness: Record<string, string>;
  current: boolean;
  ideaId: string;
  ideaVersion: number;
  builtAt: string;
  analysisVersion: string;
  sections: Partial<Record<EvidenceSectionKey, EvidenceSection>>;
}

export interface AuditRow {
  seq: number;
  at: string;
  actor: string;
  actorKind: string;
  action: string;
  previousState: string | null;
  newState: string | null;
  reason: string | null;
  correlationId: string | null;
}

export interface ApprovalDashboard {
  counts: Record<ApprovalStatus, number>;
  pending: ApprovalRequest[];
  managementPending: number;
  recentHandoffs: { executionRequestId: string; kind: string; status: string; approvalId: string | null; createdAt: string }[];
  statistics: {
    total: number;
    approved: number;
    rejected: number;
    expired: number;
    invalidated: number;
    executed: number;
    executionFailed: number;
    approvalRate: number | null;
    averageDecisionSeconds: number | null;
    medianDecisionSeconds: number | null;
  };
  generatedAt: string;
}

export interface Confirmation {
  symbol: string;
  direction: string;
  entry: string;
  stopLoss: string;
  takeProfit: string;
  positionSize: string | null;
  maxRiskAmount: string | null;
}

export interface ApproveBody {
  clientRequestId: string;
  requestVersion: number;
  evidenceHash: string;
  acknowledgedWarnings: string[];
  confirmation: Confirmation;
  comment?: string;
}

export interface RejectBody {
  clientRequestId: string;
  requestVersion: number;
  reasonCode: string;
  comment?: string;
}

export interface ModifyBody {
  clientRequestId: string;
  requestVersion: number;
  kinds: string[];
  changes: Partial<Record<'entry' | 'stopLoss' | 'takeProfit' | 'positionSize' | 'direction', string>>;
  comment?: string;
}

export interface ApprovalPolicyView {
  version: number;
  policy: {
    expiry: { byTimeframe: Record<string, number>; minMinutes: number; maxMinutes: number; expiringSoonMinutes: number; honourIdeaExpiry: boolean };
    review: {
      riskReviewPolicy: 'acknowledge' | 'block';
      riskMaxAgeSeconds: number;
      marketDataMaxAgeSeconds: number;
      rejectionReasons: string[];
      requireCommentOnApprove: boolean;
      multiPerson: { enabled: boolean; requiredApprovals: number };
    };
    management: { expiryMinutes: number; allowStopWidening: boolean; emergency: { confirmationPhrase: string; allowSelfAuthorisation: boolean } };
    notifications: { channels: Record<string, boolean> };
    [k: string]: unknown;
  };
  history: { version: number; reason: string; createdBy: string; createdAt: string }[];
}

// ---- trade management -------------------------------------------------------------------------------------------------
export type ManagementAction =
  | 'MOVE_STOP_LOSS'
  | 'MOVE_TAKE_PROFIT'
  | 'BREAKEVEN'
  | 'TRAILING_STOP'
  | 'CLOSE'
  | 'PARTIAL_CLOSE'
  | 'CANCEL_PENDING_ORDER'
  | 'MODIFY_PENDING_ORDER'
  | 'EMERGENCY_EXIT';

export interface ManagedPosition {
  positionId: string;
  accountLogin: string | null;
  symbol: string;
  side: 'BUY' | 'SELL';
  volume: number;
  entryPrice: number;
  currentPrice: number | null;
  stopLoss: number | null;
  takeProfit: number | null;
  unrealizedPnl: number | null;
  status: string;
  openedAt: string | null;
  source: string;
  lastUpdated: string | null;
  approvalId: string | null;
}

export interface PositionsView {
  source: string;
  asOf: string | null;
  ageSeconds: number | null;
  stale: boolean;
  error: string | null;
  note: string | null;
  positions: ManagedPosition[];
}

export interface ManagementRequest {
  requestId: string;
  positionId: string;
  accountLogin: string | null;
  symbol: string;
  action: ManagementAction;
  params: Record<string, string>;
  positionSnapshot: Record<string, unknown>;
  reason: string;
  riskImplications: Record<string, string> | null;
  priority: 'normal' | 'emergency';
  requestedBy: string;
  status: string;
  version: number;
  createdAt: string;
  expiresAt: string;
  statusReason: string | null;
}
