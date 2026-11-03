import { GENERAL_SHORTCUTS, GO_TO } from '@/config/shortcuts';
import { useUiStore } from '@/stores/uiStore';
import { Drawer } from '@/components/common/Drawer';

function Keys({ keys }: { keys: string[] }) {
  return (
    <span className="flex items-center gap-1">
      {keys.map((k, i) => (
        <kbd key={i} className="min-w-[1.5rem] rounded-md border border-line-control bg-base-raised px-1.5 py-0.5 text-center font-mono text-xs text-ink">
          {k}
        </kbd>
      ))}
    </span>
  );
}

export function ShortcutsDialog() {
  const open = useUiStore((s) => s.shortcutsOpen);
  const setOpen = useUiStore((s) => s.setShortcutsOpen);
  return (
    <Drawer open={open} onClose={() => setOpen(false)} title="Keyboard shortcuts" subtitle="Work without the mouse. Shortcuts pause while you type in a field.">
      <section aria-labelledby="sc-general" className="mb-6">
        <h3 id="sc-general" className="group-label mb-2 text-neutral">General</h3>
        <ul className="flex flex-col">
          {GENERAL_SHORTCUTS.map((s) => (
            <li key={s.label + s.keys.join()} className="flex items-center justify-between gap-3 border-b border-line-soft py-2 text-sm text-ink last:border-0">
              {s.label}
              <Keys keys={s.keys} />
            </li>
          ))}
        </ul>
      </section>
      <section aria-labelledby="sc-go">
        <h3 id="sc-go" className="group-label mb-2 text-neutral">Go to (press G, then)</h3>
        <ul className="flex flex-col">
          {GO_TO.map((g) => (
            <li key={g.key} className="flex items-center justify-between gap-3 border-b border-line-soft py-2 text-sm text-ink last:border-0">
              {g.label}
              <Keys keys={['G', g.key.toUpperCase()]} />
            </li>
          ))}
        </ul>
      </section>
    </Drawer>
  );
}
