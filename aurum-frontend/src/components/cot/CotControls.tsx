import { Select } from '@/components/common/Select';
import { SegmentedControl } from '@/components/common/SegmentedControl';
import { DEFAULT_INSTRUMENTS } from '@/config/instruments';
import type { CotPeriod } from '@/hooks/useCotAnalysis';

interface Props {
  instrument: string;
  onInstrument: (s: string) => void;
  period: CotPeriod;
  onPeriod: (p: CotPeriod) => void;
  custom: { from: string; to: string };
  onCustom: (c: { from: string; to: string }) => void;
}

export function CotControls({ instrument, onInstrument, period, onPeriod, custom, onCustom }: Props) {
  return (
    <div className="flex flex-wrap items-end gap-3">
      <Select label="Contract" hideLabel value={instrument} onChange={onInstrument} options={DEFAULT_INSTRUMENTS.map((i) => ({ value: i.symbol, label: i.symbol }))} className="w-32" />
      <SegmentedControl
        label="Historical period"
        value={period}
        onChange={onPeriod}
        options={[
          { value: '12m', label: '12M' },
          { value: '2y', label: '2Y' },
          { value: '3y', label: '3Y' },
          { value: '4y', label: '4Y' },
          { value: 'custom', label: 'Custom' },
        ]}
      />
      {period === 'custom' && (
        <div className="flex items-end gap-2">
          <div className="flex flex-col gap-1">
            <label htmlFor="cot-from" className="text-2xs text-neutral">From</label>
            <input id="cot-from" type="date" className="field num h-8 w-36 text-xs" value={custom.from} onChange={(e) => onCustom({ ...custom, from: e.target.value })} />
          </div>
          <div className="flex flex-col gap-1">
            <label htmlFor="cot-to" className="text-2xs text-neutral">To</label>
            <input id="cot-to" type="date" className="field num h-8 w-36 text-xs" value={custom.to} onChange={(e) => onCustom({ ...custom, to: e.target.value })} />
          </div>
        </div>
      )}
    </div>
  );
}
