import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ErrorBoundary } from '@/components/common/ErrorBoundary';
import { isStaleBuildError, reloadForNewBuild } from '@/utils/staleBuild';

const real = window.location;
function stubReload() {
  const reload = vi.fn();
  Object.defineProperty(window, 'location', { configurable: true, value: { ...real, reload } });
  return reload;
}

afterEach(() => {
  Object.defineProperty(window, 'location', { configurable: true, value: real });
  sessionStorage.clear();
  vi.restoreAllMocks();
});

describe('a page file from an older deployment', () => {
  it('is told apart from a real page error', () => {
    expect(isStaleBuildError(new TypeError('Failed to fetch dynamically imported module: https://x/assets/RiskManagementPage-Cz6KUaqP.js'))).toBe(true);
    expect(isStaleBuildError(new TypeError('Importing a module script failed.'))).toBe(true);
    expect(isStaleBuildError(new TypeError("Cannot read properties of undefined (reading 'tone')"))).toBe(false);
  });

  it('reloads once to load the new version, and says so instead of blaming the data', () => {
    const reload = stubReload();
    vi.spyOn(console, 'error').mockImplementation(() => {});
    function Lazy(): JSX.Element {
      throw new TypeError('Failed to fetch dynamically imported module: https://aurum-ai-pi.vercel.app/assets/RiskManagementPage-Cz6KUaqP.js');
    }
    render(
      <ErrorBoundary what="this page">
        <Lazy />
      </ErrorBoundary>,
    );
    expect(screen.getByRole('alert')).toHaveTextContent('AURUM was updated.');
    expect(screen.queryByText(/unexpected shape/)).not.toBeInTheDocument();
    expect(reload).toHaveBeenCalledTimes(1);
  });

  it('never reloads in a loop', () => {
    const reload = stubReload();
    expect(reloadForNewBuild()).toBe(true);
    expect(reloadForNewBuild()).toBe(false);
    expect(reload).toHaveBeenCalledTimes(1);
  });
});
