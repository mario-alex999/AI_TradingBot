import { useAuthStore } from '@/stores/authStore';
import { hasPermission } from '@/utils/permissions';
import type { Permission } from '@/types/auth';

export function usePermission(permission: Permission): boolean {
  const role = useAuthStore((s) => s.user?.role);
  return hasPermission(role, permission);
}
