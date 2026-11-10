/** Module 08 helpers shared by the orchestration components. */
export const TEST_WORKFLOW_ID = 'aurum-core-test-echo';

export const shortId = (id: string | null | undefined): string => (id ? id.slice(0, 8) : '—');
