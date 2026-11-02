import type { ReactNode } from 'react';
import { usePermission } from '@/hooks/usePermission';
import type { Permission } from '@/types/auth';

/** Hides children unless the current role holds `permission`. UI only — the backend authorises. */
export function PermissionGate({ permission, children, fallback = null }: { permission: Permission; children: ReactNode; fallback?: ReactNode }) {
  return usePermission(permission) ? <>{children}</> : <>{fallback}</>;
}
