import type { ReactNode } from 'react';
import { Navigate, useLocation } from 'react-router-dom';
import { ShieldOff } from 'lucide-react';
import { useAuthStore } from '@/stores/authStore';
import { hasPermission } from '@/utils/permissions';
import { EmptyState } from '@/components/common/EmptyState';
import type { Permission } from '@/types/auth';

export function RequireAuth({ children }: { children: ReactNode }) {
  const user = useAuthStore((s) => s.user);
  const location = useLocation();
  if (!user) return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  return <>{children}</>;
}

export function RequirePermission({ permission, children }: { permission: Permission; children: ReactNode }) {
  const role = useAuthStore((s) => s.user?.role);
  if (!hasPermission(role, permission))
    return (
      <EmptyState
        icon={<ShieldOff className="h-5 w-5" aria-hidden />}
        title="Access restricted"
        description="Your role does not include access to this area. Ask an administrator to update your permissions."
      />
    );
  return <>{children}</>;
}
