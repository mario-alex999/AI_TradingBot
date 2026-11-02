import { ApiError } from '@/services/api';
import { env } from '@/config/env';

/** Maps any failure to user-facing copy that distinguishes offline, missing and failing endpoints. */
export function describeError(error: unknown): { title: string; description: string; kind: string } {
  if (error instanceof ApiError) {
    switch (error.kind) {
      case 'network':
        return { kind: error.kind, title: 'Backend disconnected', description: `No response from ${env.apiBaseUrl}. Start the AURUM AI backend or enable demo mode in Settings.` };
      case 'timeout':
        return { kind: error.kind, title: 'Request timed out', description: error.message };
      case 'not_implemented':
        return { kind: error.kind, title: 'Awaiting backend module', description: `${error.endpoint ?? 'This endpoint'} is not available yet. ${error.message}.` };
      case 'validation':
        return { kind: error.kind, title: 'Unexpected response', description: error.message };
      default:
        return { kind: error.kind, title: 'Request failed', description: error.message };
    }
  }
  return { kind: 'unknown', title: 'Something went wrong', description: error instanceof Error ? error.message : 'Unknown error' };
}
