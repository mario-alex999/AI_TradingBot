import { useMutation, useQueryClient } from '@tanstack/react-query';
import { exportMarketData, triggerCollection, updateSpecification, type SpecificationUpdate } from '@/services/dataService';
import { describeError } from '@/utils/errors';
import { useSettingsStore } from '@/stores/settingsStore';
import { toast } from '@/stores/toastStore';
import { saveBlob } from '@/utils/download';
import { usePermission } from './usePermission';
import { useCapability, useHealth } from './queries';

export interface ActionGate {
  enabled: boolean;
  reason?: string;
}

/**
 * Data Centre operations. Each action is enabled only when (a) mock mode is
 * off, (b) the backend advertises the capability in /health and (c) the
 * user's role allows it. The backend re-checks authorisation regardless.
 */
export function useDataActions() {
  const qc = useQueryClient();
  const mock = useSettingsStore((s) => s.mockMode);
  const health = useHealth();
  const canCollect = usePermission('data:collect');
  const canExport = usePermission('data:export');
  const canConfigure = usePermission('config:instruments');
  const capCollect = useCapability('market.collect');
  const capCsv = useCapability('market.export.csv');
  const capParquet = useCapability('market.export.parquet');
  const capSpecs = useCapability('market.specifications.write');

  const gate = (perm: boolean, cap: boolean, permMsg: string): ActionGate => {
    if (mock) return { enabled: false, reason: 'Unavailable in demo-data mode' };
    if (health.isError) return { enabled: false, reason: 'Backend unreachable' };
    if (!cap) return { enabled: false, reason: 'Endpoint not exposed by the backend yet' };
    if (!perm) return { enabled: false, reason: permMsg };
    return { enabled: true };
  };

  const collect = useMutation({
    mutationFn: triggerCollection,
    onSuccess: (r) => {
      toast({ tone: 'success', title: 'Collection started', description: `Job ${r.jobId}: ${r.status}` });
      void qc.invalidateQueries({ queryKey: ['collection-logs'] });
      void qc.invalidateQueries({ queryKey: ['data-quality'] });
    },
    onError: (e) => toast({ tone: 'error', title: 'Collection request failed', description: describeError(e).description }),
  });

  const exportData = useMutation({
    mutationFn: (format: 'csv' | 'parquet') => exportMarketData(format, {}),
    onSuccess: (blob, format) => {
      saveBlob(blob, `aurum_market_data_${new Date().toISOString().slice(0, 10)}.${format}`);
      toast({ tone: 'success', title: `Export ready (${format.toUpperCase()})` });
    },
    onError: (e) => toast({ tone: 'error', title: 'Export failed', description: describeError(e).description }),
  });

  const saveSpec = useMutation({
    mutationFn: ({ symbol, body }: { symbol: string; body: SpecificationUpdate }) => updateSpecification(symbol, body),
    onSuccess: (_r, v) => {
      toast({ tone: 'success', title: `${v.symbol} configuration saved` });
      void qc.invalidateQueries({ queryKey: ['specs'] });
      void qc.invalidateQueries({ queryKey: ['instruments'] });
    },
    onError: (e) => toast({ tone: 'error', title: 'Save failed', description: describeError(e).description }),
  });

  const checkConnection = async () => {
    const r = await health.refetch();
    if (r.isError) toast({ tone: 'error', title: 'Backend unreachable', description: describeError(r.error).description });
    else toast({ tone: r.data?.mt5 === 'connected' ? 'success' : 'warning', title: `MT5: ${r.data?.mt5 ?? 'unknown'}` });
  };

  const refreshMarketData = async () => {
    await qc.invalidateQueries({ predicate: (q) => ['quotes', 'candles', 'data-quality', 'collection-logs', 'specs'].includes(String(q.queryKey[0])) });
    toast({ tone: 'info', title: 'Market data refreshed' });
  };

  return {
    gates: {
      collect: gate(canCollect, capCollect, 'Requires the Administrator role'),
      csv: gate(canExport, capCsv, 'Your role cannot export data'),
      parquet: gate(canExport, capParquet, 'Your role cannot export data'),
      configure: gate(canConfigure, capSpecs, 'Requires the Administrator role'),
    },
    collect,
    exportData,
    saveSpec,
    checkConnection,
    refreshMarketData,
    checking: health.isFetching,
  };
}
