import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { canServer, useAuthStore } from '@/stores/authStore';
import { connectMt5, disconnectMt5, getMt5Connection, getMt5Status, type Mt5ConnectionResponse } from '@/services/mt5Service';

export const MT5_LINK_PERMISSION = 'mt5:link';
const KEY = ['mt5-connection'] as const;

export function useCanLinkMt5(): boolean {
  const user = useAuthStore((s) => s.user);
  return canServer(user, MT5_LINK_PERMISSION);
}

/** This sign-in session's link. Polls quickly while it is changing state, slowly once settled. */
export function useMt5Connection() {
  const enabled = useCanLinkMt5();
  return useQuery({
    queryKey: KEY,
    queryFn: ({ signal }) => getMt5Connection(signal),
    enabled,
    retry: false,
    refetchInterval: (q) => {
      const s = q.state.data?.connection?.status;
      return s === 'CONNECTING' || s === 'DISCONNECTING' ? 1500 : 30_000;
    },
  });
}

export function useMt5ServerStatus(enabled: boolean) {
  return useQuery({ queryKey: ['mt5-status'], queryFn: ({ signal }) => getMt5Status(signal), enabled, retry: false, staleTime: 15_000 });
}

export function useMt5Connect() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { login: string; password: string; server: string }) => connectMt5(v.login, v.password, v.server),
    onSuccess: (data: Mt5ConnectionResponse) => qc.setQueryData(KEY, (old: Mt5ConnectionResponse | undefined) => ({ ...old, ...data })),
  });
}

export function useMt5Disconnect() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: disconnectMt5,
    onSuccess: (data: Mt5ConnectionResponse) => qc.setQueryData(KEY, (old: Mt5ConnectionResponse | undefined) => ({ ...old, ...data })),
  });
}
