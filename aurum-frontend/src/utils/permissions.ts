import { ROLE_PERMISSIONS } from '@/config/permissions';
import type { Permission, Role } from '@/types/auth';

export function hasPermission(role: Role | null | undefined, permission: Permission): boolean {
  if (!role) return false;
  return ROLE_PERMISSIONS[role].includes(permission);
}
