import { useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { CornerDownLeft, Search, Star } from 'lucide-react';
import { ALL_NAV_ITEMS } from '@/config/navigation';
import { ASSET_CLASS_LABEL, DEFAULT_INSTRUMENTS } from '@/config/instruments';
import { useInstruments } from '@/hooks/queries';
import { useAuthStore } from '@/stores/authStore';
import { useUiStore } from '@/stores/uiStore';
import { useWatchlistStore } from '@/stores/watchlistStore';
import { hasPermission } from '@/utils/permissions';
import { cn } from '@/utils/cn';

interface Entry {
  id: string;
  group: 'Instruments' | 'Pages';
  label: string;
  hint: string;
  to: string;
  favourite?: boolean;
}

/** Quick search (⌘K / Ctrl+K): jump to any page or instrument from the keyboard. */
export function CommandPalette() {
  const open = useUiStore((s) => s.paletteOpen);
  const setOpen = useUiStore((s) => s.setPaletteOpen);
  const role = useAuthStore((s) => s.user?.role);
  const favourites = useWatchlistStore((s) => s.favourites);
  const instruments = useInstruments();
  const navigate = useNavigate();
  const [query, setQuery] = useState('');
  const [active, setActive] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        setOpen(!useUiStore.getState().paletteOpen);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [setOpen]);

  useEffect(() => {
    if (open) {
      setQuery('');
      setActive(0);
      requestAnimationFrame(() => inputRef.current?.focus());
    }
  }, [open]);

  const entries = useMemo<Entry[]>(() => {
    const list = instruments.data?.data ?? DEFAULT_INSTRUMENTS;
    const inst: Entry[] = list.map((i) => ({
      id: `i-${i.symbol}`,
      group: 'Instruments',
      label: i.symbol,
      hint: `${i.name} · ${ASSET_CLASS_LABEL[i.assetClass]}`,
      to: `/market/${i.symbol}`,
      favourite: favourites.includes(i.symbol),
    }));
    const pages: Entry[] = ALL_NAV_ITEMS.filter((n) => hasPermission(role, n.permission)).map((n) => ({
      id: `p-${n.path}`,
      group: 'Pages',
      label: n.label,
      hint: 'Page',
      to: n.path,
    }));
    const q = query.trim().toLowerCase();
    const match = (e: Entry) => !q || e.label.toLowerCase().includes(q) || e.hint.toLowerCase().includes(q);
    return [...inst.filter(match).sort((a, b) => Number(!!b.favourite) - Number(!!a.favourite)), ...pages.filter(match)];
  }, [instruments.data, favourites, role, query]);

  useEffect(() => setActive(0), [query]);

  if (!open) return null;

  const go = (e: Entry | undefined) => {
    if (!e) return;
    setOpen(false);
    navigate(e.to);
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setActive((a) => Math.min(entries.length - 1, a + 1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setActive((a) => Math.max(0, a - 1));
    } else if (e.key === 'Enter') {
      e.preventDefault();
      go(entries[active]);
    } else if (e.key === 'Escape') {
      setOpen(false);
    }
  };

  let lastGroup = '';
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center px-4 pt-[12vh]" role="dialog" aria-modal="true" aria-label="Quick search">
      <button type="button" aria-label="Close search" className="absolute inset-0 bg-black/30" onClick={() => setOpen(false)} />
      <div className="relative w-full max-w-lg animate-fade-in overflow-hidden rounded-2xl bg-base-card shadow-float" onKeyDown={onKeyDown}>
        <div className="flex items-center gap-3 border-b border-line-soft px-4">
          <Search className="h-4 w-4 text-neutral" aria-hidden />
          <label htmlFor="palette-input" className="sr-only">
            Search pages and instruments
          </label>
          <input
            id="palette-input"
            ref={inputRef}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search pages and instruments"
            className="h-12 flex-1 bg-transparent text-sm text-ink outline-none placeholder:text-neutral focus-visible:ring-0 focus-visible:ring-offset-0"
            role="combobox"
            aria-expanded="true"
            aria-controls="palette-list"
            aria-activedescendant={entries[active]?.id}
            autoComplete="off"
          />
          <kbd className="rounded-md bg-base-raised px-1.5 py-0.5 text-2xs text-neutral">Esc</kbd>
        </div>
        <ul id="palette-list" role="listbox" className="max-h-[50vh] overflow-y-auto p-2">
          {entries.length === 0 && <li className="px-3 py-6 text-center text-sm text-neutral">No pages or instruments match “{query}”.</li>}
          {entries.map((e, i) => {
            const header = e.group !== lastGroup ? e.group : null;
            lastGroup = e.group;
            return (
              <li key={e.id} role="presentation">
                {header && <p className="group-label px-3 pb-1 pt-2 text-neutral">{header}</p>}
                <div
                  id={e.id}
                  role="option"
                  aria-selected={i === active}
                  onMouseEnter={() => setActive(i)}
                  onClick={() => go(e)}
                  className={cn(
                    'flex cursor-pointer items-center gap-3 rounded-lg px-3 py-2',
                    i === active ? 'bg-gold text-white' : 'text-ink',
                  )}
                >
                  <span className="text-sm font-medium">{e.label}</span>
                  {e.favourite && <Star className={cn('h-3.5 w-3.5', i === active ? 'fill-white text-white' : 'fill-amber-400 text-amber-400')} aria-label="Favourite" />}
                  <span className={cn('ml-auto truncate text-xs', i === active ? 'text-white/75' : 'text-neutral')}>{e.hint}</span>
                  {i === active && <CornerDownLeft className="h-3.5 w-3.5 shrink-0 text-white/75" aria-hidden />}
                </div>
              </li>
            );
          })}
        </ul>
      </div>
    </div>
  );
}
