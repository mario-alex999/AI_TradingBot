/**
 * After a new deployment, a tab that was already open still runs the previous build. When it opens a page it has not
 * loaded yet, it asks for that build's page file (e.g. assets/RiskManagementPage-<hash>.js), which the new deployment
 * no longer has. The fix is to load the new build: reload once.
 */
const KEY = 'aurum:stale-build-reload';
const WINDOW_MS = 30_000;

/** True when an error means "this build's file is gone", not a bug in the page. */
export function isStaleBuildError(error: unknown): boolean {
  const msg = error instanceof Error ? `${error.name} ${error.message}` : String(error);
  return /Failed to fetch dynamically imported module|Importing a module script failed|error loading dynamically imported module|ChunkLoadError|Unable to preload CSS/i.test(
    msg,
  );
}

/** Reloads the page to pick up the new build, at most once every 30 s (so a real outage cannot loop). */
export function reloadForNewBuild(): boolean {
  let last = 0;
  try {
    last = Number(sessionStorage.getItem(KEY) ?? 0);
  } catch {
    /* storage blocked: still reload once */
  }
  if (Date.now() - last < WINDOW_MS) return false;
  try {
    sessionStorage.setItem(KEY, String(Date.now()));
  } catch {
    /* ignore */
  }
  window.location.reload();
  return true;
}

/** Vite fires vite:preloadError when a lazily loaded file cannot be fetched: reload instead of failing. */
export function installStaleBuildReload(): void {
  window.addEventListener('vite:preloadError', (event) => {
    if (reloadForNewBuild()) event.preventDefault();
  });
}
