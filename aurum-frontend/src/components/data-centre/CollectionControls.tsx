import { useState } from 'react';
import { Activity, Download, Play, RefreshCw } from 'lucide-react';
import { Card, CardBody, CardHeader } from '@/components/common/Card';
import { Button } from '@/components/common/Button';
import { ConfirmDialog } from '@/components/common/ConfirmDialog';
import { useDataActions } from '@/hooks/useDataActions';
import { DEFAULT_INSTRUMENTS, TIMEFRAMES } from '@/config/instruments';

export function CollectionControls() {
  const a = useDataActions();
  const [confirm, setConfirm] = useState(false);
  return (
    <Card>
      <CardHeader
        icon={<Activity className="h-4 w-4" />}
        title="Data collection controls"
        subtitle="Operational actions are enabled only when the backend exposes them and your role permits."
      />
      <CardBody className="flex flex-col gap-3">
        <div className="flex flex-wrap gap-2">
          <Button icon={<Activity className="h-4 w-4" aria-hidden />} onClick={() => void a.checkConnection()} loading={a.checking}>
            Check MT5 connection
          </Button>
          <Button
            variant="primary"
            icon={<Play className="h-4 w-4" aria-hidden />}
            disabled={!a.gates.collect.enabled}
            disabledReason={a.gates.collect.reason}
            loading={a.collect.isPending}
            onClick={() => setConfirm(true)}
          >
            Start collection
          </Button>
          <Button icon={<RefreshCw className="h-4 w-4" aria-hidden />} onClick={() => void a.refreshMarketData()}>
            Refresh market data
          </Button>
          <Button
            icon={<Download className="h-4 w-4" aria-hidden />}
            disabled={!a.gates.csv.enabled}
            disabledReason={a.gates.csv.reason}
            loading={a.exportData.isPending && a.exportData.variables === 'csv'}
            onClick={() => a.exportData.mutate('csv')}
          >
            Export CSV
          </Button>
          <Button
            icon={<Download className="h-4 w-4" aria-hidden />}
            disabled={!a.gates.parquet.enabled}
            disabledReason={a.gates.parquet.reason}
            loading={a.exportData.isPending && a.exportData.variables === 'parquet'}
            onClick={() => a.exportData.mutate('parquet')}
          >
            Export Parquet
          </Button>
        </div>
        {!a.gates.collect.enabled && (
          <p className="text-2xs text-neutral" data-testid="collect-disabled-reason">
            Start collection: {a.gates.collect.reason}.
          </p>
        )}
      </CardBody>
      <ConfirmDialog
        open={confirm}
        title="Start market data collection?"
        description={
          <>
            The backend will request {TIMEFRAMES.join(', ')} candles for {DEFAULT_INSTRUMENTS.map((i) => i.symbol).join(', ')} from MT5 and store them after
            validation.
          </>
        }
        confirmLabel="Start collection"
        onCancel={() => setConfirm(false)}
        onConfirm={() => {
          setConfirm(false);
          a.collect.mutate({});
        }}
      />
    </Card>
  );
}
