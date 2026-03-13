import { useEffect, useRef, useState } from 'react';

export interface PaletteCommand {
  id: string;
  label: string;
  icon: React.ReactNode;
  action: () => void;
  badge?: string;
  badgeActive?: boolean; // true = green, false = muted
  destructive?: boolean;
}

interface Props {
  open: boolean;
  onClose: () => void;
  commands: PaletteCommand[];
}

export function CommandPalette({ open, onClose, commands }: Props) {
  const [query, setQuery] = useState('');
  const [activeIndex, setActiveIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  const filtered = query.trim()
    ? commands.filter(c => c.label.toLowerCase().includes(query.toLowerCase()))
    : commands;

  // Reset state when opening
  useEffect(() => {
    if (open) {
      setQuery('');
      setActiveIndex(0);
      setTimeout(() => inputRef.current?.focus(), 10);
    }
  }, [open]);

  // Keep activeIndex in bounds when filter changes
  useEffect(() => {
    setActiveIndex(0);
  }, [query]);

  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { onClose(); return; }
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        setActiveIndex(i => Math.min(i + 1, filtered.length - 1));
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        setActiveIndex(i => Math.max(i - 1, 0));
      } else if (e.key === 'Enter') {
        e.preventDefault();
        if (filtered[activeIndex]) {
          filtered[activeIndex].action();
          onClose();
        }
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [open, filtered, activeIndex, onClose]);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center pt-[20vh]"
      onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      {/* Backdrop */}
      <div className="absolute inset-0 bg-black/40" onMouseDown={onClose} />

      {/* Panel */}
      <div className="relative w-full max-w-md mx-4 rounded-xl border bg-background shadow-2xl overflow-hidden">
        {/* Search input */}
        <div className="flex items-center gap-2 px-4 py-3 border-b">
          <svg className="h-4 w-4 text-muted-foreground shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <circle cx="11" cy="11" r="8" /><path d="m21 21-4.35-4.35" />
          </svg>
          <input
            ref={inputRef}
            className="flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground"
            placeholder="Search commands…"
            value={query}
            onChange={e => setQuery(e.target.value)}
          />
          <kbd className="text-xs text-muted-foreground border rounded px-1 py-0.5">esc</kbd>
        </div>

        {/* Command list */}
        <div className="py-1 max-h-72 overflow-auto">
          {filtered.length === 0 ? (
            <p className="text-xs text-muted-foreground text-center py-6">No commands found</p>
          ) : (
            filtered.map((cmd, i) => (
              <button
                key={cmd.id}
                className={`w-full flex items-center gap-3 px-4 py-2.5 text-sm text-left transition-colors
                  ${i === activeIndex ? 'bg-accent text-accent-foreground' : 'hover:bg-accent/50'}
                  ${cmd.destructive ? 'text-destructive' : ''}`}
                onMouseEnter={() => setActiveIndex(i)}
                onClick={() => { cmd.action(); onClose(); }}
              >
                <span className="text-muted-foreground shrink-0">{cmd.icon}</span>
                <span className="flex-1">{cmd.label}</span>
                {cmd.badge !== undefined && (
                  <span className={`text-xs px-1.5 py-0.5 rounded font-medium ${
                    cmd.badgeActive
                      ? 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400'
                      : 'bg-muted text-muted-foreground'
                  }`}>
                    {cmd.badge}
                  </span>
                )}
              </button>
            ))
          )}
        </div>
      </div>
    </div>
  );
}
