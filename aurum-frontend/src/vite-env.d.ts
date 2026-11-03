/// <reference types="vite/client" />

declare const __APP_VERSION__: string;
declare const __BUILD_TIME__: string;
declare const __COMMIT__: string;

interface ImportMetaEnv {
  readonly VITE_API_BASE_URL?: string;
  readonly VITE_ENABLE_MOCK_DATA?: string;
  readonly VITE_API_TIMEOUT_MS?: string;
  readonly VITE_REALTIME_URL?: string;
  readonly VITE_ROUTER?: 'browser' | 'memory';
  readonly VITE_BUILT_BY?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
