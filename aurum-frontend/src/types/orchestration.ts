/** Module 08 (workflow orchestration) API shapes, camelCase as served by /api/v1/orchestration. */

export type ExecutionStatus =
  | 'RECEIVED'
  | 'VALIDATING'
  | 'QUEUED'
  | 'DISPATCHED'
  | 'RUNNING'
  | 'WAITING_CALLBACK'
  | 'SUCCEEDED'
  | 'FAILED'
  | 'TIMED_OUT'
  | 'RETRY_PENDING'
  | 'DEAD_LETTERED'
  | 'CANCELLED';

export type WorkflowState = 'REGISTERED' | 'VALIDATED' | 'ACTIVE' | 'PAUSED' | 'DEGRADED' | 'DISABLED' | 'RETIRED';
export type HealthStatus = 'HEALTHY' | 'DEGRADED' | 'UNAVAILABLE' | 'UNKNOWN';

export interface Workflow {
  workflowId: string;
  name: string;
  description: string | null;
  purpose: string | null;
  category: string;
  owner: string | null;
  environment: string;
  engine: string;
  n8nWorkflowId: string | null;
  webhookPath: string | null;
  state: WorkflowState;
  triggerTypes: string[];
  activeVersionId: string | null;
  activeVersion: string | null;
  timeoutSeconds: number;
  retryPolicy: Record<string, unknown> | null;
  idempotentDispatch: boolean;
  placeholder: boolean;
  healthStatus: string | null;
  healthDetail: string | null;
  rowVersion: number;
  createdAt: string;
  updatedAt: string | null;
  lastExecutionAt: string | null;
}

export interface WorkflowVersion {
  versionId: string;
  workflowId: string;
  version: string;
  description: string | null;
  status: string;
  contractVersion: string;
  validation: Record<string, unknown> | null;
  createdBy: string;
  createdAt: string;
  activatedAt: string | null;
}

export interface Execution {
  executionId: string;
  workflowId: string;
  workflowVersion: string;
  environment: string;
  status: ExecutionStatus;
  triggerType: string;
  triggerSource: string;
  correlationId: string;
  causationId: string | null;
  idempotencyKey: string;
  symbol: string | null;
  tradeIdeaId: string | null;
  approvalId: string | null;
  n8nExecutionId: string | null;
  attempt: number;
  retryCount: number;
  nextAttemptAt: string | null;
  deadlineAt: string | null;
  errorCategory: string | null;
  errorMessage: string | null;
  warnings: string[];
  createdAt: string;
  startedAt: string | null;
  finishedAt: string | null;
  durationSeconds: number | null;
}

export interface TimelineEntry {
  at: string;
  from: string | null;
  to: string;
  by: string;
  reason: string | null;
  attempt: number;
}

export interface ExecutionDetail extends Execution {
  input: Record<string, unknown>;
  output: Record<string, unknown> | null;
  timeline: TimelineEntry[];
  attempts: { attempt: number; startedAt: string; finishedAt: string | null; outcome: string; httpStatus: number | null;
    latencyMs: number | null; errorCategory: string | null; errorMessage: string | null }[];
  callbacks: { callbackId: string; kind: string; attempt: number | null; receivedAt: string; outcome: string; detail: string | null;
    previousStatus: string | null; newStatus: string | null }[];
}

export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  pageSize: number;
}

export interface OrchEvent {
  sequence: number;
  eventId: string;
  eventType: string;
  eventVersion: string;
  occurredAt: string;
  publishedAt: string;
  sourceModule: string;
  correlationId: string;
  causationId: string | null;
  environment: string;
  payload: Record<string, unknown>;
  metadata: Record<string, unknown>;
  ingress: string;
  status: string;
  deliveriesTotal: number;
}

export interface Delivery {
  deliveryId: number;
  subscription: string;
  target: string;
  replay: number;
  status: string;
  attempts: number;
  lastError: string | null;
  errorCategory: string | null;
  executionId: string | null;
  deliveredAt: string | null;
}

export interface DeadLetter {
  id: string;
  kind: 'execution' | 'delivery';
  eventId: string | null;
  executionId: string | null;
  workflowId: string | null;
  errorCategory: string;
  reason: string;
  deliveryCount: number;
  failedAt: string;
  correlationId: string | null;
  recoveryStatus: string;
  resolvedBy: string | null;
  resolutionNote: string | null;
}

export interface Integration {
  integrationId: string;
  name: string;
  kind: string;
  baseUrl: string | null;
  status: HealthStatus;
  detail: string | null;
  lastCheckedAt: string | null;
  lastHealthyAt: string | null;
  latencyMs: number | null;
  consecutiveFailures: number;
  circuit: { state: string } | null;
}

export interface Overview {
  environment: string;
  moduleVersion: string;
  workflows: { total: number; active: number; paused: number; byState: Record<string, number> };
  executions: { running: number; queued: number; retryPending: number; succeeded: number; failed: number; deadLettered: number };
  last24h: Last24h;
  engine: { engine?: string; label?: string; apiConfigured?: boolean; capabilities?: Record<string, string> };
  n8nPublicUrl: string;
  deadLettersOpen: number;
  deliveriesPending: number;
  integrations: Record<string, { status: HealthStatus; detail: string | null; checkedAt: string | null }>;
  recentExecutions: Execution[];
  alerts: { level: 'warning' | 'critical'; message: string }[];
}

export interface Last24h {
  executions: number;
  finished: number;
  successRate: number | null;
  avgDurationSeconds: number | null;
  events: number;
  avgDeliveryLatencyMs: number | null;
  rejectedCallbacks: number;
}

export interface SecretStatus {
  name: string;
  source: string;
  purpose: string;
  configured: boolean;
  fingerprint: string | null;
  rotatedAt: string | null;
}

export interface ConfigView {
  version: number;
  /** The configuration document, with snake_case keys exactly as stored (sent back unchanged to PUT /config). */
  config: Record<string, unknown>;
  reason: string;
  createdBy: string;
  createdAt: string;
  history: { version: number; reason: string; createdBy: string; createdAt: string }[];
  secrets: SecretStatus[];
  connection: Record<string, string | number | null>;
  note: string;
}

export interface AuditRow {
  seq: number;
  at: string;
  actor: string;
  actorKind: string;
  action: string;
  entityType: string;
  entityId: string;
  outcome: string;
  correlationId: string | null;
  details: Record<string, unknown> | null;
}
