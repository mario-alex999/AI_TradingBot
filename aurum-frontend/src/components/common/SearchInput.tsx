import { Search } from 'lucide-react';

export function SearchInput({ value, onChange, label, placeholder }: { value: string; onChange: (v: string) => void; label: string; placeholder?: string }) {
  return (
    <div className="relative">
      <label className="sr-only" htmlFor={`search-${label}`}>
        {label}
      </label>
      <Search className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-neutral" aria-hidden />
      <input
        id={`search-${label}`}
        type="search"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder ?? label}
        className="field pl-8"
      />
    </div>
  );
}
