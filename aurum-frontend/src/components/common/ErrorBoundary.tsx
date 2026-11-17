import { Component, type ErrorInfo, type ReactNode } from 'react';
import { AlertTriangle } from 'lucide-react';

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
    console.error('AURUM: a panel failed to render', error, info.componentStack);
  }

  componentDidUpdate(prev: Props) {
    if (this.state.error && prev.resetKey !== this.props.resetKey) this.setState({ error: null });
  }

  render() {
    if (!this.state.error) return this.props.children;
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
