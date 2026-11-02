import { useState } from 'react';
import { useFieldArray, useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { z } from 'zod';
import { Button } from '@/components/common/Button';
import { ConfirmDialog } from '@/components/common/ConfirmDialog';
import { DEFAULT_SESSIONS, type SessionDefinition } from '@/config/sessions';
import { useSettingsStore } from '@/stores/settingsStore';
import { toast } from '@/stores/toastStore';

const isValidZone = (tz: string) => {
  try {
    new Intl.DateTimeFormat('en-US', { timeZone: tz });
    return true;
  } catch {
    return false;
  }
};
const hm = z.string().regex(/^([01]\d|2[0-3]):[0-5]\d$/, 'Use HH:mm');
const schema = z.object({
  sessions: z.array(
    z.object({
      id: z.enum(['asia', 'london', 'newyork']),
      name: z.string(),
      timeZone: z.string().refine(isValidZone, 'Unknown IANA timezone'),
      open: hm,
      close: hm,
    }),
  ),
});
type Values = z.infer<typeof schema>;

/** Edits session definitions (stored locally). Hours are local to each session's own IANA zone. */
export function SessionConfigForm() {
  const sessions = useSettingsStore((s) => s.sessions);
  const update = useSettingsStore((s) => s.update);
  const [pending, setPending] = useState<SessionDefinition[] | null>(null);
  const {
    register,
    control,
    handleSubmit,
    reset,
    formState: { errors, isDirty },
  } = useForm<Values>({ resolver: zodResolver(schema), values: { sessions } });
  const { fields } = useFieldArray({ control, name: 'sessions' });

  return (
    <form onSubmit={handleSubmit((v) => setPending(v.sessions))} className="flex flex-col gap-3" noValidate>
      {fields.map((f, i) => (
        <fieldset key={f.id} className="grid gap-2 rounded-md border border-line-soft p-3 sm:grid-cols-[minmax(0,1.4fr)_90px_90px]">
          <legend className="px-1 text-xs font-medium text-ink">{f.name}</legend>
          <div className="flex flex-col gap-1">
            <label htmlFor={`s-${i}-tz`} className="text-2xs text-neutral">
              Timezone (IANA)
            </label>
            <input id={`s-${i}-tz`} className="field num h-8 text-xs" {...register(`sessions.${i}.timeZone`)} />
            {errors.sessions?.[i]?.timeZone && <span className="text-2xs text-down-text">{errors.sessions[i]?.timeZone?.message}</span>}
          </div>
          <div className="flex flex-col gap-1">
            <label htmlFor={`s-${i}-open`} className="text-2xs text-neutral">
              Opens
            </label>
            <input id={`s-${i}-open`} className="field num h-8 text-xs" {...register(`sessions.${i}.open`)} />
            {errors.sessions?.[i]?.open && <span className="text-2xs text-down-text">{errors.sessions[i]?.open?.message}</span>}
          </div>
          <div className="flex flex-col gap-1">
            <label htmlFor={`s-${i}-close`} className="text-2xs text-neutral">
              Closes
            </label>
            <input id={`s-${i}-close`} className="field num h-8 text-xs" {...register(`sessions.${i}.close`)} />
            {errors.sessions?.[i]?.close && <span className="text-2xs text-down-text">{errors.sessions[i]?.close?.message}</span>}
          </div>
        </fieldset>
      ))}
      <div className="flex flex-wrap gap-2">
        <Button type="submit" variant="primary" size="sm" disabled={!isDirty}>
          Save sessions
        </Button>
        <Button size="sm" variant="ghost" onClick={() => reset({ sessions: DEFAULT_SESSIONS })}>
          Restore defaults
        </Button>
      </div>
      <ConfirmDialog
        open={pending !== null}
        title="Update session definitions?"
        description="Session panels, overlap monitoring and session statistics will use the new hours. Stored in this browser only."
        confirmLabel="Save sessions"
        onCancel={() => setPending(null)}
        onConfirm={() => {
          if (pending) update({ sessions: pending });
          setPending(null);
          toast({ tone: 'success', title: 'Session definitions updated' });
        }}
      />
    </form>
  );
}
