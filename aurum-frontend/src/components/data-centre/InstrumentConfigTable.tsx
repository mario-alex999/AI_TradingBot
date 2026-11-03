import { useState } from 'react';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { z } from 'zod';
import { Pencil, SlidersHorizontal } from 'lucide-react';
import { Card, CardHeader } from '@/components/common/Card';
import { DataTable, type Column } from '@/components/common/DataTable';
import { DataSourceBadge } from '@/components/common/DataSourceBadge';
import { QueryState } from '@/components/common/QueryState';
import { Badge } from '@/components/common/Badge';
import { Button } from '@/components/common/Button';
import { ConfirmDialog } from '@/components/common/ConfirmDialog';
import { EmptyState } from '@/components/common/EmptyState';
import { useSpecifications } from '@/hooks/queries';
import { useDataActions } from '@/hooks/useDataActions';
import { ASSET_CLASS_LABEL } from '@/config/instruments';
import { formatNumber, PLACEHOLDER } from '@/utils/format';
import type { InstrumentSpecification } from '@/types/market';

const schema = z.object({
  brokerSymbol: z.string().trim().min(1, 'Required').max(32).regex(/^[A-Za-z0-9._#-]+$/, 'Letters, digits and . _ # - only'),
  enabled: z.boolean(),
});
type FormValues = z.infer<typeof schema>;

function EditRow({ spec, onDone }: { spec: InstrumentSpecification; onDone: () => void }) {
  const { saveSpec } = useDataActions();
  const [pending, setPending] = useState<FormValues | null>(null);
  const {
    register,
    handleSubmit,
    formState: { errors, isDirty },
  } = useForm<FormValues>({ resolver: zodResolver(schema), defaultValues: { brokerSymbol: spec.brokerSymbol ?? spec.symbol, enabled: spec.enabled } });
  return (
    <form onSubmit={handleSubmit(setPending)} className="flex flex-wrap items-end gap-3 border-t border-line bg-base-raised p-3" aria-label={`Edit ${spec.symbol}`}>
      <div className="flex flex-col gap-1">
        <label htmlFor="brokerSymbol" className="text-xs text-ink-muted">
          Broker symbol for <span className="num text-ink">{spec.symbol}</span>
        </label>
        <input id="brokerSymbol" className="field num w-44" aria-invalid={!!errors.brokerSymbol} {...register('brokerSymbol')} />
        {errors.brokerSymbol && <span className="text-2xs text-down-text">{errors.brokerSymbol.message}</span>}
      </div>
      <label className="flex h-9 items-center gap-2 text-sm text-ink">
        <input type="checkbox" className="h-4 w-4 accent-[#F2F2F3]" {...register('enabled')} /> Enabled
      </label>
      <div className="ml-auto flex gap-2">
        <Button variant="ghost" onClick={onDone}>
          Cancel
        </Button>
        <Button type="submit" variant="primary" disabled={!isDirty} loading={saveSpec.isPending}>
          Save
        </Button>
      </div>
      <ConfirmDialog
        open={pending !== null}
        title={`Update ${spec.symbol} configuration?`}
        description="This change is sent to the backend and affects market data collection for this instrument."
        confirmLabel="Save change"
        onCancel={() => setPending(null)}
        onConfirm={() => {
          if (pending) saveSpec.mutate({ symbol: spec.symbol, body: pending }, { onSuccess: onDone });
          setPending(null);
        }}
      />
    </form>
  );
}

export function InstrumentConfigTable() {
  const specs = useSpecifications();
  const { gates } = useDataActions();
  const [editing, setEditing] = useState<string | null>(null);
  const editingSpec = specs.data?.data.find((s) => s.symbol === editing);

  const columns: Column<InstrumentSpecification>[] = [
    { key: 'sym', header: 'Canonical', sortValue: (r) => r.symbol, cell: (r) => <span className="num font-semibold text-ink">{r.symbol}</span> },
    { key: 'broker', header: 'Broker symbol', cell: (r) => <span className="num text-ink-muted">{r.brokerSymbol ?? PLACEHOLDER}</span> },
    { key: 'class', header: 'Asset class', sortValue: (r) => r.assetClass, cell: (r) => <span className="text-ink-muted">{ASSET_CLASS_LABEL[r.assetClass]}</span> },
    { key: 'en', header: 'Enabled', align: 'center', cell: (r) => (r.enabled ? <Badge tone="up">On</Badge> : <Badge>Off</Badge>) },
    { key: 'digits', header: 'Digits', align: 'right', cell: (r) => <span className="num">{r.digits ?? PLACEHOLDER}</span> },
    { key: 'point', header: 'Point', align: 'right', cell: (r) => <span className="num">{r.point != null ? r.point.toString() : PLACEHOLDER}</span> },
    { key: 'cs', header: 'Contract size', align: 'right', cell: (r) => <span className="num">{formatNumber(r.contractSize, 0)}</span> },
    { key: 'ts', header: 'Tick size', align: 'right', cell: (r) => <span className="num">{r.tickSize != null ? r.tickSize.toString() : PLACEHOLDER}</span> },
    { key: 'tv', header: 'Tick value', align: 'right', cell: (r) => <span className="num">{formatNumber(r.tickValue, 2)}</span> },
    {
      key: 'edit',
      header: '',
      align: 'right',
      cell: (r) => (
        <Button
          size="sm"
          variant="ghost"
          aria-label={`Edit ${r.symbol}`}
          icon={<Pencil className="h-3.5 w-3.5" aria-hidden />}
          disabled={!gates.configure.enabled}
          disabledReason={gates.configure.reason}
          onClick={() => setEditing(r.symbol)}
        >
          Edit
        </Button>
      ),
    },
  ];

  return (
    <Card>
      <CardHeader
        icon={<SlidersHorizontal className="h-4 w-4" />}
        title="Instrument configuration"
        subtitle="Specifications are read from MT5 by the backend. Changes are saved through the backend API."
        actions={<DataSourceBadge source={specs.data?.source} fetchedAt={specs.data?.fetchedAt} />}
      />
      <QueryState query={specs} isEmpty={(d) => d.data.length === 0} empty={<EmptyState compact title="No instruments configured" />}>
        {(d) => <DataTable caption="Instrument specifications" dense columns={columns} rows={d.data} rowKey={(r) => r.symbol} />}
      </QueryState>
      {editingSpec && <EditRow key={editingSpec.symbol} spec={editingSpec} onDone={() => setEditing(null)} />}
    </Card>
  );
}
