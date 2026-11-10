import { useEffect, useState } from 'react';
import { CheckCircle2, Loader2, ShieldAlert, ShieldCheck } from 'lucide-react';
import { Button } from '@/components/common/Button';
import { Modal } from '@/components/approvals/ApprovalBits';
import { useMt5Connect, useMt5Connection } from '@/hooks/useMt5Link';
import { describeError } from '@/utils/errors';
import type { Mt5Connection } from '@/services/mt5Service';

/**
 * "Connect your MT5 account". Shown after a server sign-in (and from the top bar). The person authorises their own DEMO
 * account; the password goes to the backend once and is cleared from this form straight away. AURUM only reads the
 * account (balance, deals, positions) and never trades from this link.
 */
export function Mt5AuthorizeDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const conn = useMt5Connection();
  const connect = useMt5Connect();
  const scope = conn.data?.accountScope ?? null;
  const [login, setLogin] = useState('');
  const [server, setServer] = useState('');
  const [password, setPassword] = useState('');
  const [submitted, setSubmitted] = useState(false);

  useEffect(() => {
    if (!open) return;
    setSubmitted(false);
    setPassword('');
    connect.reset();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- reset only when the dialog opens
  }, [open]);

  useEffect(() => {
    if (!login && scope?.[0]) setLogin(scope[0]);
  }, [scope, login]);

  const link = conn.data?.connection ?? null;
  const showProgress = submitted && connect.isSuccess && !!link;

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!login.trim() || !server.trim() || !password) return;
    const pw = password;
    setPassword('');
    setSubmitted(true);
    connect.mutate({ login: login.trim(), password: pw, server: server.trim() });
  };

  return (
    <Modal open={open} title="Connect your MT5 account" onClose={onClose}>
      {showProgress && link ? (
        <Progress link={link} onRetry={() => setSubmitted(false)} onClose={onClose} />
      ) : (
        <form className="flex flex-col gap-3" onSubmit={submit} aria-label="Authorise MT5 account">
          <div className="flex gap-2 rounded-md bg-base-raised p-3 text-xs text-ink-muted">
            <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-neutral" aria-hidden />
            <span>
              Authorise AURUM to read your MetaTrader 5 <strong className="text-ink">DEMO</strong> account so your balance, positions and
              history sync here. AURUM only reads the account; it never places trades from this link. Your password is used once to log
              the terminal in and is not stored.
            </span>
          </div>
          <label className="flex flex-col gap-1 text-xs font-medium text-ink-muted" htmlFor="mt5-login">
            Account number
            {scope && scope.length > 1 ? (
              <select id="mt5-login" className="field" value={login} onChange={(e) => setLogin(e.target.value)}>
                {scope.map((a) => (
                  <option key={a} value={a}>
                    {a}
                  </option>
                ))}
              </select>
            ) : (
              <input id="mt5-login" className="field num" inputMode="numeric" autoComplete="off" value={login}
                readOnly={!!scope && scope.length === 1} onChange={(e) => setLogin(e.target.value.replace(/\D/g, ''))} required />
            )}
          </label>
          <label className="flex flex-col gap-1 text-xs font-medium text-ink-muted" htmlFor="mt5-server">
            Broker server
            <input id="mt5-server" className="field" autoComplete="off" placeholder="e.g. MetaQuotes-Demo" value={server}
              onChange={(e) => setServer(e.target.value)} maxLength={128} required />
            <span className="font-normal text-neutral">Exactly as MetaTrader shows it at login.</span>
          </label>
          <label className="flex flex-col gap-1 text-xs font-medium text-ink-muted" htmlFor="mt5-password">
            MT5 password
            <input id="mt5-password" type="password" className="field" autoComplete="off" value={password}
              onChange={(e) => setPassword(e.target.value)} maxLength={128} required />
            <span className="font-normal text-neutral">The investor (read-only) password is enough and is the safer choice.</span>
          </label>
          {connect.isError && (
            <p role="alert" className="flex gap-2 rounded-md border border-down/40 bg-down-faint p-2.5 text-xs text-down-text">
              <ShieldAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
              {describeError(connect.error).description}
            </p>
          )}
          <div className="mt-1 flex justify-end gap-2">
            <Button type="button" variant="ghost" size="sm" onClick={onClose}>
              Skip for now
            </Button>
            <Button type="submit" variant="primary" size="sm" disabled={connect.isPending || !login || !server.trim() || !password}
              disabledReason={connect.isPending ? 'Authorising' : 'Fill in the account, server and password'}>
              {connect.isPending ? 'Authorising…' : 'Authorise'}
            </Button>
          </div>
        </form>
      )}
    </Modal>
  );
}

function Progress({ link, onRetry, onClose }: { link: Mt5Connection; onRetry: () => void; onClose: () => void }) {
  if (link.status === 'CONNECTING')
    return (
      <div className="flex flex-col gap-4" aria-live="polite">
        <p className="flex items-center gap-2 text-sm text-ink">
          <Loader2 className="h-4 w-4 animate-spin text-gold" aria-hidden />
          Logging in to account <span className="num">{link.login}</span> on {link.server}…
        </p>
        <p className="text-xs text-ink-muted">This can take up to a minute while the terminal starts.</p>
        <div className="flex justify-end">
          <Button type="button" variant="ghost" size="sm" onClick={onClose}>
            Continue in the background
          </Button>
        </div>
      </div>
    );
  if (link.status === 'CONNECTED')
    return (
      <div className="flex flex-col gap-4" aria-live="polite">
        <p className="flex items-start gap-2 text-sm text-ink">
          <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-up" aria-hidden />
          <span>
            Connected to account <span className="num">{link.login}</span>
            {link.company ? ` at ${link.company}` : ''} ({link.tradeMode ?? 'DEMO'}). {link.lastSyncAt ? 'Your account has synced.' : 'Syncing your account now.'}
          </span>
        </p>
        <div className="flex justify-end">
          <Button type="button" variant="primary" size="sm" onClick={onClose}>
            Done
          </Button>
        </div>
      </div>
    );
  return (
    <div className="flex flex-col gap-4" aria-live="polite">
      <p role="alert" className="flex gap-2 rounded-md border border-down/40 bg-down-faint p-2.5 text-xs text-down-text">
        <ShieldAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
        {link.errorMessage ?? (link.status === 'DISCONNECTING' ? 'The previous link is closing; try again in a moment.' : 'The account could not be linked.')}
      </p>
      <div className="flex justify-end gap-2">
        <Button type="button" variant="ghost" size="sm" onClick={onClose}>
          Close
        </Button>
        <Button type="button" variant="primary" size="sm" onClick={onRetry}>
          Try again
        </Button>
      </div>
    </div>
  );
}
