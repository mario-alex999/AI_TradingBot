import { useState } from 'react';
import { History } from 'lucide-react';
import { Card, CardBody, CardHeader } from '@/components/common/Card';
import { Badge } from '@/components/common/Badge';
import { DataTable, type Column } from '@/components/common/DataTable';
import { Drawer } from '@/components/common/Drawer';
import { Pagination } from '@/components/common/Pagination';
import { Select } from '@/components/common/Select';
import { AssessmentResult } from './RiskCalculator';
import { useAssessment, useRiskHistory } from '@/hooks/useRisk';
import { DEFAULT_INSTRUMENTS } from '@/config/instruments';
import type { AssessmentSummary, RiskDecision } from '@/types/risk';
import { describeError } from '@/utils/errors';
import { formatDateTime, formatNumber, formatPercent } from '@/utils/format';
import { DECISION_LABEL, DECISION_TONE } from '@/utils/riskLabels';

const PAGE = 20;

const COLUMNS: Column<AssessmentSummary>[] = [
  { key: 'createdAt', header: 'Time', cell: (r) => formatDateTime(r.createdAt) },
  { key: 'instrument', header: 'Trade', cell: (r) => `${r.instrument} ${r.direction}` },
  { key: 'decision', header: 'Decision', cell: (r) => <Badge tone={DECISION_TONE[r.decision]}>{DECISION_LABEL[r.decision]}</Badge> },
  { key: 'volume', header: 'Size', align: 'right', cell: (r) => (r.finalVolume == null ? '—' : formatNumber(r.finalVolume, 2)) },
  { key: 'risk', header: 'Risk', align: 'right', cell: (r) => formatPercent(r.riskPct) },
  { key: 'rr', header: 'R:R', align: 'right', cell: (r) => formatNumber(r.riskReward, 2) },
  { key: 'rules', header: 'Rules hit', cell: (r) => [...r.ruleViolations, ...r.errors, ...r.warnings].join(', ') || '—' },
  { key: 'state', header: 'State', cell: (r) => r.state },
];

/** Section F: every recorded assessment, with filters and the full stored result on demand. */
export function AssessmentHistory() {
  const [page, setPage] = useState(1);
  const [decision, setDecision] = useState<RiskDecision | ''>('');
  const [instrument, setInstrument] = useState('');
  const [rule, setRule] = useState('');
  const [selected, setSelected] = useState<string | null>(null);
  const q = useRiskHistory({ decision, instrument, rule: rule.trim().toUpperCase(), limit: PAGE, offset: (page - 1) * PAGE });
  const detail = useAssessment(selected);
  const data = q.data?.data;
  const reset = (f: () => void) => {
    f();
    setPage(1);
  };

  return (
    <Card>
      <CardHeader icon={<History className="h-4 w-4" />} title="Assessment history" subtitle="Stored with the config version and every rule result. Select a row for details." />
      <CardBody className="flex flex-col gap-4">
        <div className="grid gap-3 sm:grid-cols-3">
          <Select
            label="Decision"
            value={decision}
            options={[{ value: '', label: 'All' }, ...(['PASS', 'REVIEW', 'BLOCK', 'ERROR'] as const).map((d) => ({ value: d, label: DECISION_LABEL[d] }))]}
            onChange={(v) => reset(() => setDecision(v as RiskDecision | ''))}
          />
          <Select
            label="Instrument"
            value={instrument}
            options={[{ value: '', label: 'All' }, ...DEFAULT_INSTRUMENTS.map((i) => ({ value: i.symbol, label: i.symbol }))]}
            onChange={(v) => reset(() => setInstrument(v))}
          />
          <label className="flex flex-col gap-1 text-xs text-ink-muted">
            Rule id
            <input className="field" value={rule} placeholder="e.g. R030" onChange={(e) => reset(() => setRule(e.target.value))} />
          </label>
        </div>
        {q.isError ? (
          <p className="text-xs text-neutral">{describeError(q.error).description}</p>
        ) : (
          <>
            <DataTable
              columns={COLUMNS}
              rows={data?.items ?? []}
              rowKey={(r) => r.assessmentId}
              onRowClick={(r) => setSelected(r.assessmentId)}
              rowLabel={(r) => `Assessment ${r.instrument} ${r.direction} ${r.decision}`}
              dense
              empty={data ? 'No assessments match these filters.' : 'History comes from the backend risk engine.'}
            />
            {data && data.total > PAGE && <Pagination page={page} pageSize={PAGE} total={data.total} onPage={setPage} />}
          </>
        )}
      </CardBody>
      <Drawer open={!!selected} title="Assessment" subtitle={selected ?? undefined} onClose={() => setSelected(null)} width="w-[min(94vw,560px)]">
        {detail.data?.data ? (
          <AssessmentResult a={detail.data.data} />
        ) : detail.isError ? (
          <p className="text-xs text-neutral">{describeError(detail.error).description}</p>
        ) : (
          <p className="text-xs text-neutral">Loading…</p>
        )}
      </Drawer>
    </Card>
  );
}
