import { CircleAlert, CircleHelp, X } from "lucide-react";
import { useCallback, useState, type ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

const HELP_PREFIX = "dw-help-closed:";

/** Open or closed state of the "how to read this page" note. The choice is kept per page. */
export function useHelp(page: string): [boolean, () => void] {
  const [open, setOpen] = useState(() => {
    try {
      return localStorage.getItem(HELP_PREFIX + page) !== "1";
    } catch {
      return true;
    }
  });
  const toggle = useCallback(() => {
    setOpen((was) => {
      try {
        if (was) localStorage.setItem(HELP_PREFIX + page, "1");
        else localStorage.removeItem(HELP_PREFIX + page);
      } catch {
        // Storage can be blocked. The note still opens and closes for this visit.
      }
      return !was;
    });
  }, [page]);
  return [open, toggle];
}

export function HelpButton({ open, onToggle }: { open: boolean; onToggle: () => void }) {
  return (
    <Button
      variant="ghost"
      size="icon-sm"
      onClick={onToggle}
      aria-pressed={open}
      aria-label={open ? "Hide the reading guide" : "Show the reading guide"}
      title="How to read this page"
      className={cn("text-ink-3", open && "text-foreground")}
    >
      <CircleHelp />
    </Button>
  );
}

/** The reading guide of a page. */
export function HelpNote({ open, onClose, children }: { open: boolean; onClose: () => void; children: ReactNode }) {
  if (!open) return null;
  return (
    <aside className="flex items-start gap-3 rounded-lg border border-line bg-panel px-4 py-3 text-[12.5px] leading-relaxed text-ink-2">
      <CircleHelp className="mt-0.5 size-4 shrink-0 text-ink-3" aria-hidden="true" />
      <div className="max-w-[78ch]">{children}</div>
      <Button
        variant="ghost"
        size="icon-xs"
        onClick={onClose}
        aria-label="Hide the reading guide"
        className="ml-auto shrink-0 text-ink-3"
      >
        <X />
      </Button>
    </aside>
  );
}

/** Empty or waiting state inside a region. */
export function Quiet({ children, className }: { children: ReactNode; className?: string }) {
  return <p className={cn("px-4 py-6 text-[12.5px] text-ink-3", className)}>{children}</p>;
}

/** A problem that the person must know about, with what to do next. */
export function Problem({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <p
      role="alert"
      className={cn(
        "flex items-start gap-2 rounded-md border border-st-review/30 bg-st-review/10 px-3 py-2 text-[12.5px] text-ink-2",
        className,
      )}
    >
      <CircleAlert className="mt-0.5 size-3.5 shrink-0 text-st-review-ink" aria-hidden="true" />
      <span>{children}</span>
    </p>
  );
}
