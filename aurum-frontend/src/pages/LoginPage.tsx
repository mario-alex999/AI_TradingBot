import { useState } from 'react';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { z } from 'zod';
import { Navigate, useLocation, useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Info, KeyRound, ShieldAlert } from 'lucide-react';
import { LogoMark } from '@/components/common/Logo';
import { Button } from '@/components/common/Button';
import { ROLE_LABEL } from '@/config/permissions';
import { useAuthStore } from '@/stores/authStore';
import { useMt5Store } from '@/stores/mt5Store';
import { ApiError } from '@/services/api';
import { getAuthStatus, login } from '@/services/authService';
import type { Role } from '@/types/auth';

const demoSchema = z.object({
  name: z.string().trim().min(2, 'Enter at least 2 characters').max(60),
  role: z.enum(['administrator', 'trader', 'analyst', 'viewer']),
});
type DemoValues = z.infer<typeof demoSchema>;

const serverSchema = z.object({
  username: z.string().trim().min(3, 'Enter your username').max(64),
  password: z.string().min(1, 'Enter your password').max(256),
});
type ServerValues = z.infer<typeof serverSchema>;

function Brand() {
  return (
    <div className="mb-8 flex flex-col items-center text-center">
      <LogoMark className="h-12 w-12" />
      <h1 className="mt-4 flex items-baseline gap-2">
        <span className="text-xl font-semibold tracking-[0.24em] text-ink">AURUM</span>
        <span className="font-mono text-lg font-semibold text-gold">AI</span>
      </h1>
      <p className="mt-2 text-sm text-ink-muted">Institutional market intelligence</p>
    </div>
  );
}

/**
 * Sign-in. When the backend is reachable and has accounts, people sign in with a username and password (Module 07):
 * the backend sets an httpOnly session cookie and the password is never stored in the browser. The demo workspace is
 * still available for browsing; it has no backend identity, so it cannot approve or manage trades.
 */
export default function LoginPage() {
  const user = useAuthStore((s) => s.user);
  const location = useLocation();
  const from = (location.state as { from?: string } | null)?.from ?? '/';
  const status = useQuery({ queryKey: ['auth-status'], queryFn: ({ signal }) => getAuthStatus(signal), retry: false, staleTime: 30_000 });
  const [demo, setDemo] = useState(false);

  if (user) return <Navigate to={from} replace />;

  const backendReady = status.data?.ready === true && status.data.hasUsers;
  const showServer = backendReady && !demo;

  return (
    <main className="flex min-h-screen items-center justify-center bg-base px-4 py-10">
      <div className="w-full max-w-sm">
        <Brand />
        {status.isLoading ? (
          <div className="rounded-card border border-line bg-base-card p-5 text-sm text-ink-muted shadow-card">Checking the backend…</div>
        ) : showServer ? (
          <ServerForm from={from} onDemo={() => setDemo(true)} />
        ) : (
          <DemoForm
            from={from}
            notice={
              status.data && !backendReady
                ? status.data.message ?? 'No accounts exist yet on the backend.'
                : 'Backend sign-in is unavailable (the backend is not reachable).'
            }
            onBack={backendReady ? () => setDemo(false) : undefined}
          />
        )}
        <p className="mt-6 text-center text-2xs text-neutral">Approvals need a human decision · AURUM never places trades by itself</p>
      </div>
    </main>
  );
}

function ServerForm({ from, onDemo }: { from: string; onDemo: () => void }) {
  const signInServer = useAuthStore((s) => s.signInServer);
  const navigate = useNavigate();
  const [error, setError] = useState<string | null>(null);
  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<ServerValues>({ resolver: zodResolver(serverSchema), defaultValues: { username: '', password: '' } });

  const onSubmit = async (v: ServerValues) => {
    setError(null);
    try {
      const r = await login(v.username.trim().toLowerCase(), v.password);
      signInServer(r.user, r.csrfToken);
      // Straight after sign-in, ask the person to authorise their own MT5 DEMO account (they can skip).
      if (r.user.permissions.includes('mt5:link')) useMt5Store.getState().setPromptOpen(true);
      navigate(from, { replace: true });
    } catch (e) {
      if (e instanceof ApiError && e.status === 429) setError('Too many sign-in attempts. Wait a minute and try again.');
      else if (e instanceof ApiError && e.status === 401) setError('Username or password is incorrect, or the account is locked.');
      else setError(e instanceof Error ? e.message : 'Sign-in failed.');
    }
  };

  return (
    <form onSubmit={handleSubmit(onSubmit)} className="rounded-card border border-line bg-base-card p-5 shadow-card" noValidate aria-label="Sign in">
      <div className="mb-4 flex gap-2 rounded-md bg-base-raised p-3 text-xs text-ink-muted">
        <KeyRound className="mt-0.5 h-4 w-4 shrink-0 text-neutral" aria-hidden />
        <span>Sign in with your AURUM account. Your session ends after a period of inactivity.</span>
      </div>
      <div className="flex flex-col gap-1">
        <label htmlFor="username" className="text-xs font-medium text-ink-muted">
          Username
        </label>
        <input id="username" className="field" autoComplete="username" aria-invalid={!!errors.username} {...register('username')} />
        {errors.username && <p className="text-xs text-down-text">{errors.username.message}</p>}
      </div>
      <div className="mt-4 flex flex-col gap-1">
        <label htmlFor="password" className="text-xs font-medium text-ink-muted">
          Password
        </label>
        <input id="password" type="password" className="field" autoComplete="current-password" aria-invalid={!!errors.password} {...register('password')} />
        {errors.password && <p className="text-xs text-down-text">{errors.password.message}</p>}
      </div>
      {error && (
        <p role="alert" className="mt-4 flex gap-2 rounded-md bg-down-faint p-2.5 text-xs text-down-text">
          <ShieldAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
          {error}
        </p>
      )}
      <Button type="submit" variant="primary" className="mt-5 w-full justify-center" disabled={isSubmitting} disabledReason="Signing in">
        {isSubmitting ? 'Signing in…' : 'Sign in'}
      </Button>
      <button type="button" onClick={onDemo} className="mt-3 w-full text-center text-xs text-ink-muted underline-offset-2 hover:text-ink hover:underline">
        Open the demo workspace instead (view only, no approvals)
      </button>
    </form>
  );
}

function DemoForm({ from, notice, onBack }: { from: string; notice: string; onBack?: () => void }) {
  const signInDemo = useAuthStore((s) => s.signInDemo);
  const navigate = useNavigate();
  const {
    register,
    handleSubmit,
    formState: { errors },
  } = useForm<DemoValues>({ resolver: zodResolver(demoSchema), defaultValues: { name: 'Demo Trader', role: 'administrator' } });

  const onSubmit = (v: DemoValues) => {
    signInDemo(v.name, v.role as Role);
    navigate(from, { replace: true });
  };

  return (
    <form onSubmit={handleSubmit(onSubmit)} className="rounded-card border border-line bg-base-card p-5 shadow-card" noValidate aria-label="Demo workspace">
      <div className="mb-4 flex gap-2 rounded-md bg-base-raised p-3 text-xs text-ink-muted">
        <Info className="mt-0.5 h-4 w-4 shrink-0 text-neutral" aria-hidden />
        <span>
          Demo workspace. No password is required or stored. {notice} Trade approvals need a signed-in backend account.
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
      {onBack && (
        <button type="button" onClick={onBack} className="mt-3 w-full text-center text-xs text-ink-muted underline-offset-2 hover:text-ink hover:underline">
          Back to account sign-in
        </button>
      )}
    </form>
  );
}
