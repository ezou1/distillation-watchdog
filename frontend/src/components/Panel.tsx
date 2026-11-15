import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

/** A bordered region of a page. Use it for the main regions only, not around every small item. */
export function Panel({ className, children }: { className?: string; children: ReactNode }) {
  return <section className={cn("rounded-xl border border-line bg-panel", className)}>{children}</section>;
}

export function PanelHeader({
  title,
  detail,
  actions,
  className,
}: {
  title: ReactNode;
  /** Short text after the title: a count or a unit. */
  detail?: ReactNode;
  actions?: ReactNode;
  className?: string;
}) {
  return (
    <header className={cn("flex min-h-12 flex-wrap items-center gap-x-3 gap-y-2 border-b border-line px-5 py-2", className)}>
      <h2 className="text-[13.5px] font-semibold tracking-[-0.01em]">{title}</h2>
      {detail && <span className="text-[12.5px] text-ink-3">{detail}</span>}
      {actions && <div className="ml-auto flex flex-wrap items-center gap-2">{actions}</div>}
    </header>
  );
}

/** Heading inside a panel or page for a sub-part. */
export function SectionLabel({ children, className }: { children: ReactNode; className?: string }) {
  return <h3 className={cn("micro", className)}>{children}</h3>;
}
