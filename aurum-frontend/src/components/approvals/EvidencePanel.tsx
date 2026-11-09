import { useState } from 'react';
import { Badge, type Tone } from '@/components/common/Badge';
import { Tabs } from '@/components/common/Tabs';
import { formatDateTime } from '@/utils/format';
import type { Evidence, EvidenceItem, EvidenceKind, EvidenceSectionKey } from '@/types/approvals';

const SECTION_LABEL: Record<EvidenceSectionKey, string> = {
  trade: 'Trade',
  technical: 'Technical',
  market: 'Market',
  institutional: 'Institutional',
  ai: 'AI review',
  risk: 'Risk',
};

/** Every item says what kind of evidence it is, so a confirmed fact is never confused with an AI opinion. */
const KIND: Record<EvidenceKind, { label: string; tone: Tone }> = {
  confirmed: { label: 'Confirmed data', tone: 'up' },
  ai_interpretation: { label: 'AI interpretation', tone: 'info' },
  historical: { label: 'Historical', tone: 'neutral' },
  contradictory: { label: 'Contradicts the trade', tone: 'warn' },
  risk_warning: { label: 'Risk warning', tone: 'down' },
  missing: { label: 'Missing', tone: 'neutral' },
};

function show(v: unknown): string {
  if (v === null || v === undefined || v === '') return '—';
  if (Array.isArray(v)) return v.map(show).join(', ');
  if (typeof v === 'object') return JSON.stringify(v);
  return String(v);
}

function Row({ item }: { item: EvidenceItem }) {
  const k = KIND[item.kind] ?? KIND.historical;
  return (
    <li className="flex flex-col gap-1 border-b border-line-soft py-2 last:border-0 sm:flex-row sm:items-start sm:gap-3">
      <span className="w-40 shrink-0 text-xs text-neutral">{item.label}</span>
      <span className={item.kind === 'missing' ? 'flex-1 text-sm italic text-neutral' : 'num flex-1 break-words text-sm text-ink'}>
        {item.kind === 'missing' ? item.reason ?? 'Not available' : show(item.value)}
      </span>
      <span className="flex shrink-0 flex-wrap items-center gap-1.5">
        <Badge tone={k.tone}>{k.label}</Badge>
        {item.source && <span className="text-2xs text-neutral">{item.source}</span>}
      </span>
    </li>
  );
}

export function EvidencePanel({ evidence }: { evidence: Evidence }) {
  const keys = (Object.keys(SECTION_LABEL) as EvidenceSectionKey[]).filter((k) => evidence.sections[k]);
  const [tab, setTab] = useState<EvidenceSectionKey>(keys[0] ?? 'trade');
  const sec = evidence.sections[tab];
  return (
    <div>
      <Tabs
        label="Evidence sections"
        value={tab}
        onChange={setTab}
        tabs={keys.map((k) => ({ id: k, label: `${SECTION_LABEL[k]}${evidence.sections[k]?.status === 'missing' ? ' (missing)' : ''}` }))}
      />
      {sec && (
        <div className="pt-3">
          <p className="text-xs text-neutral">
            Source: {sec.source}
            {sec.asOf && <> · as of {formatDateTime(sec.asOf)} UTC</>}
            {sec.status !== 'available' && <> · {sec.status === 'missing' ? 'not available' : 'partly available'}</>}
            {sec.reason && <> · {sec.reason}</>}
          </p>
          <ul className="mt-1">
            {sec.items.map((it, i) => (
              <Row key={`${it.label}-${i}`} item={it} />
            ))}
          </ul>
        </div>
      )}
      <p className="mt-3 text-2xs text-neutral">
        Evidence snapshot {evidence.snapshotId.slice(0, 8)} · built {formatDateTime(evidence.builtAt)} UTC · fingerprint{' '}
        <span className="num">{evidence.hash.slice(0, 12)}</span>. AI output and confidence scores are not predictions of profit.
      </p>
    </div>
  );
}
