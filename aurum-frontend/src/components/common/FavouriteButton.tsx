import { Star } from 'lucide-react';
import { useWatchlistStore } from '@/stores/watchlistStore';
import { cn } from '@/utils/cn';

export function FavouriteButton({ symbol, className }: { symbol: string; className?: string }) {
  const on = useWatchlistStore((s) => s.favourites.includes(symbol));
  const toggle = useWatchlistStore((s) => s.toggle);
  return (
    <button
      type="button"
      aria-pressed={on}
      aria-label={on ? `Remove ${symbol} from favourites` : `Add ${symbol} to favourites`}
      title={on ? 'Remove from favourites' : 'Add to favourites'}
      onClick={(e) => {
        e.preventDefault();
        e.stopPropagation();
        toggle(symbol);
      }}
      className={cn('relative z-10 flex h-8 w-8 items-center justify-center rounded-full transition hover:bg-base-raised active:scale-90', className)}
    >
      <Star className={cn('h-4 w-4 transition-colors', on ? 'fill-amber-400 text-amber-400' : 'text-line-control hover:text-neutral')} aria-hidden />
    </button>
  );
}
