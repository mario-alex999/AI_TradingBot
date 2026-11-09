export type Role = 'administrator' | 'trader' | 'analyst' | 'viewer';

export type Permission =
  | 'view:dashboard'
  | 'view:trading'
  | 'view:risk'
  | 'view:system'
  | 'data:collect'
  | 'data:export'
  | 'config:instruments'
  | 'config:agents'
  | 'signals:review'
  | 'risk:manage'
  | 'settings:security';

export interface User {
  id: string;
  name: string;
  email: string;
  role: Role;
  /** 'server' = signed in with a password (httpOnly session cookie); 'demo' = local demo profile, no backend identity. */
  mode?: 'demo' | 'server';
  username?: string;
  /** Backend role (admin | trader | reviewer | observer) and its permissions, e.g. 'approvals:decide'. */
  serverRole?: string;
  permissions?: string[];
  accountScope?: string[] | null;
}

/** Module 07 backend permissions. The UI uses them only to decide what to show; the backend checks every request. */
export type ServerPermission =
  | 'approvals:view'
  | 'approvals:decide'
  | 'approvals:create'
  | 'approvals:cancel'
  | 'approvals:revalidate'
  | 'approvals:policy'
  | 'approvals:audit'
  | 'management:request'
  | 'management:decide'
  | 'management:emergency';
