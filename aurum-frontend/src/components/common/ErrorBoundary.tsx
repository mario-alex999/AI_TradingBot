import { Component, type ErrorInfo, type ReactNode } from 'react';
import { AlertTriangle, RefreshCw } from 'lucide-react';
import { isStaleBuildError, reloadForNewBuild } from '@/utils/staleBuild';

interface Props {
  children: ReactNode;
  /** What failed, in the user's words, e.g. "this signal's details". */
  what?: string;
  /** Changing this value clears the error (for example, opening another signal). */
  resetKey?: unknown;
}

interface State {
  error: Error | null;
}

/**
 * Keeps one broken panel from blanking the whole screen. React unmounts the entire tree on an uncaught render error;
 * this shows a short message in place of the failed part and leaves the rest of the page working.
 */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    if (isStaleBuildError(error)) {
      reloadForNewBuild(); // a new version was deployed: load it (at most once every 30 s)
      return;
    }
    console.error('AURUM: a panel failed to render', error, info.componentStack);
  }

  componentDidUpdate(prev: Props) {
    if (this.state.error && prev.resetKey !== this.props.resetKey) this.setState({ error: null });
  }

  render() {
    if (!this.state.error) return this.props.children;
    if (isStaleBuildError(this.state.error)) {
      return (
        <div role="alert" className="flex items-center gap-3 rounded-card border border-info/30 bg-info-faint px-4 py-3 text-sm text-ink-muted">
          <RefreshCw className="h-4 w-4 shrink-0 text-info" aria-hidden />
          <p className="flex-1">
            <span className="font-medium text-ink">AURUM was updated.</span> Reload to open this page with the new version.
          </p>
          <button type="button" className="rounded-full border border-line-control px-3 py-1 text-xs text-ink hover:bg-base-raised" onClick={() => window.location.reload()}>
            Reload
          </button>
        </div>
      );
    }
    return (
      <div role="alert" className="flex gap-2 rounded-card border border-down/30 bg-down-faint px-4 py-3 text-sm text-ink-muted">
        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-down-text" aria-hidden />
        <div>
          <p className="font-medium text-ink">Could not show {this.props.what ?? 'this section'}.</p>
          <p className="text-xs">The data arrived in an unexpected shape. The rest of the page still works. Details: {this.state.error.message}</p>
        </div>
      </div>
    );
  }
}
