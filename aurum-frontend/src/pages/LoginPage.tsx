import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { z } from 'zod';
import { Navigate, useLocation, useNavigate } from 'react-router-dom';
import { Info } from 'lucide-react';
import { LogoMark } from '@/components/common/Logo';
import { Button } from '@/components/common/Button';
import { ROLE_LABEL } from '@/config/permissions';
import { useAuthStore } from '@/stores/authStore';
import type { Role } from '@/types/auth';

const schema = z.object({
  name: z.string().trim().min(2, 'Enter at least 2 characters').max(60),
  role: z.enum(['administrator', 'trader', 'analyst', 'viewer']),
});
type FormValues = z.infer<typeof schema>;

/**
 * Authentication-ready entry screen. This demo build has no backend auth, so
 * it opens a local demo workspace with a chosen role and never asks for a
 * password. Replace `signInDemo` with a call to the backend's login endpoint
 * (which should set an httpOnly cookie) when it exists.
 */
export default function LoginPage() {
  const user = useAuthStore((s) => s.user);
  const signInDemo = useAuthStore((s) => s.signInDemo);
  const navigate = useNavigate();
  const location = useLocation();
  const from = (location.state as { from?: string } | null)?.from ?? '/';
  const {
    register,
    handleSubmit,
    formState: { errors },
  } = useForm<FormValues>({ resolver: zodResolver(schema), defaultValues: { name: 'Demo Trader', role: 'administrator' } });

  if (user) return <Navigate to={from} replace />;

  const onSubmit = (v: FormValues) => {
    signInDemo(v.name, v.role as Role);
    navigate(from, { replace: true });
  };

  return (
    <main className="flex min-h-screen items-center justify-center bg-base px-4 py-10">
      <div className="w-full max-w-sm">
        <div className="mb-8 flex flex-col items-center text-center">
          <LogoMark className="h-12 w-12" />
          <h1 className="mt-4 flex items-baseline gap-2">
            <span className="text-xl font-semibold tracking-[0.24em] text-ink">AURUM</span>
            <span className="font-mono text-lg font-semibold text-gold">AI</span>
          </h1>
          <p className="mt-2 text-sm text-ink-muted">Institutional market intelligence</p>
        </div>
        <form onSubmit={handleSubmit(onSubmit)} className="rounded-card border border-line bg-base-card p-5 shadow-card" noValidate>
          <div className="mb-4 flex gap-2 rounded-md bg-base-raised p-3 text-xs text-ink-muted">
            <Info className="mt-0.5 h-4 w-4 shrink-0 text-neutral" aria-hidden />
            <span>
              Demo workspace. No password is required or stored. Backend authentication will replace this screen.
            </span>
          </div>
          <div className="flex flex-col gap-1">
            <label htmlFor="name" className="text-xs font-medium text-ink-muted">
              Display name
            </label>
            <input id="name" className="field" autoComplete="name" aria-invalid={!!errors.name} {...register('name')} />
            {errors.name && <p className="text-xs text-down-text">{errors.name.message}</p>}
          </div>
          <div className="mt-4 flex flex-col gap-1">
            <label htmlFor="role" className="text-xs font-medium text-ink-muted">
              Role
            </label>
            <select id="role" className="field" {...register('role')}>
              {(Object.keys(ROLE_LABEL) as Role[]).map((r) => (
                <option key={r} value={r}>
                  {ROLE_LABEL[r]}
                </option>
              ))}
            </select>
          </div>
          <Button type="submit" variant="primary" className="mt-5 w-full justify-center">
            Enter demo workspace
          </Button>
        </form>
        <p className="mt-6 text-center text-2xs text-neutral">Read-only interface · no order execution</p>
      </div>
    </main>
  );
}
