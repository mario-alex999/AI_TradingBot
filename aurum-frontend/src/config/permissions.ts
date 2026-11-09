import type { Permission, Role } from '@/types/auth';

/**
 * UI-side permission map. It only decides what to SHOW — the backend must
 * authorise every request independently.
 */
export const ROLE_PERMISSIONS: Record<Role, Permission[]> = {
  administrator: [
    'view:dashboard',
    'view:trading',
    'view:risk',
    'view:system',
    'data:collect',
    'data:export',
    'config:instruments',
    'config:agents',
    'signals:review',
    'risk:manage',
    'settings:security',
  ],
  trader: ['view:dashboard', 'view:trading', 'view:risk', 'data:export', 'signals:review'],
  analyst: ['view:dashboard', 'view:trading', 'view:risk', 'data:export'],
  viewer: ['view:dashboard'],
};

export const ROLE_LABEL: Record<Role, string> = {
  administrator: 'Administrator',
  trader: 'Trader',
  analyst: 'Analyst',
  viewer: 'Viewer',
};
