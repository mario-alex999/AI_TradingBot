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
  | 'settings:security';

export interface User {
  id: string;
  name: string;
  email: string;
  role: Role;
}
