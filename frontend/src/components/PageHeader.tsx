import type { ReactNode } from "react";

/** The header row of a page: where you are on the left, the actions of the page on the right. */
export function PageHeader({ title, trail, actions }: { title: ReactNode; trail?: ReactNode; actions?: ReactNode }) {
  return (
    <header className="flex min-h-12 flex-wrap items-center gap-x-3 gap-y-2 border-b border-line px-4 py-2 sm:px-6">
      <div className="flex min-w-0 items-center gap-2 text-[13.5px]">
        {trail && (
          <>
            <span className="shrink-0 text-ink-3">{trail}</span>
            <span className="text-ink-4" aria-hidden="true">
              ›
            </span>
          </>
        )}
        <span className="truncate font-semibold tracking-[-0.01em]">{title}</span>
      </div>
      {actions && <div className="ml-auto flex flex-wrap items-center gap-2">{actions}</div>}
    </header>
  );
}

/** The scrolling body of a page. */
export function PageBody({ children }: { children: ReactNode }) {
  // minmax(0, 1fr): a wide table scrolls inside its panel instead of widening the page on a phone.
  return <div className="grid grid-cols-[minmax(0,1fr)] gap-5 p-4 sm:p-6">{children}</div>;
}
