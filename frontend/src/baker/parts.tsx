// Small shared pieces of Baker Street: the words, the state stamp, the pin, and the frame of a display.

import { Popover } from "radix-ui";
import { createContext, useContext, type ReactNode } from "react";

import type { AccountState } from "@/api";
import { routeHref, type Route } from "@/lib/router";
import { detectorMeta, parseReason, stateMeta } from "@/lib/states";

// ---------- words ----------

/** The three signal colors. Five backend states map to them. */
export type Tone = "red" | "yellow" | "gray";

export function toneOf(state: string | null | undefined): Tone {
  if (state === "block") return "red";
  if (state === "review") return "yellow";
  return "gray";
}

/** Order of severity, most severe first. */
const SEVERITY: Record<string, number> = { block: 0, review: 1, normal: 2, disclosed: 3, insufficient: 4 };

export function moreSevere(a: AccountState, b: AccountState): AccountState {
  return (SEVERITY[a] ?? 9) <= (SEVERITY[b] ?? 9) ? a : b;
}

const LINK_WORDS: Record<string, string> = {
  shared_task: "same task text",
  sync: "same timing",
  rare_schema: "same rare tool set",
};

/** A link type of the account graph, in plain words. */
export function linkWord(type: string): string {
  return LINK_WORDS[type] ?? type.replace(/_/g, " ");
}

/** Detector names as short plain words for the accounts list. */
const SIGN_WORDS: Record<string, string> = {
  Redundancy: "Repeated requests",
  Engagement: "Answers not read",
  Coverage: "Many topics",
  Elicitation: "Asks for full reasoning",
  Templates: "Template prompts",
  Environment: "Generated tool outputs",
  Volume: "High volume",
  "Campaign link": "Linked accounts",
};

/** The detectors behind the top reasons of an account, in plain words and without repeats. */
export function reasonWords(reasons: string[] | undefined, rulesLevel?: string | null): string[] {
  const names = new Set<string>();
  // The rules score is the share of repeated contexts. It can flag an account by itself.
  if (rulesLevel === "block" || rulesLevel === "review") names.add("Repeated requests");
  for (const reason of reasons ?? []) {
    const name = detectorMeta(parseReason(reason).feature).name;
    if (name !== "Other") names.add(SIGN_WORDS[name] ?? name);
  }
  return [...names];
}

export function plural(count: number, one: string, many: string): string {
  return `${count.toLocaleString("en-US")} ${count === 1 ? one : many}`;
}

// ---------- stamp ----------

/** The state of an account or campaign as a rubber-stamp mark. The word is always there; color never stands alone. */
export function Stamp({ state, className = "" }: { state: string | null | undefined; className?: string }) {
  const meta = stateMeta(state);
  return (
    <span className={`bk-stamp bk-stamp--${toneOf(state)} ${className}`} title={meta.meaning}>
      {meta.label}
    </span>
  );
}

/** A small square of the signal color, for legends. */
export function Swatch({ tone }: { tone: Tone }) {
  return <span className={`bk-swatch bk-swatch--${tone}`} aria-hidden="true" />;
}

// ---------- pin ----------

/** A drawing pin, seen from above. `data-bk-pin` lets the board find where a string attaches. */
export function Pin({ id, className = "" }: { id?: string; className?: string }) {
  return (
    <svg className={`bk-pin ${className}`} data-bk-pin={id} viewBox="0 0 20 20" aria-hidden="true">
      <ellipse cx="11.5" cy="13" rx="6.5" ry="5.5" fill="var(--bk-ink)" opacity="0.28" />
      <circle cx="10" cy="10" r="6.5" fill="var(--bk-red)" stroke="var(--bk-red-deep)" strokeWidth="1" />
      <circle cx="8" cy="7.8" r="2" fill="var(--bk-paper)" opacity="0.55" />
    </svg>
  );
}

// ---------- the frame of a display ----------

export function DisplayFrame({
  title,
  note,
  back,
  backLabel = "Back to the board",
  actions,
  children,
}: {
  title: ReactNode;
  /** One short sentence under the title. */
  note?: ReactNode;
  /** Where Back goes. */
  back: Route;
  backLabel?: string;
  actions?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="bk-display">
      <header className="bk-display__head">
        <a className="bk-back" href={routeHref(back)} data-bk-back aria-label={backLabel} title={`${backLabel} (Esc)`}>
          <svg viewBox="0 0 24 12" width="24" height="12" aria-hidden="true">
            <path d="M7 1 L1 6 L7 11 M1 6 H23" fill="none" stroke="currentColor" strokeWidth="1.6" />
          </svg>
          Back
        </a>
        <div className="bk-display__title bk-perf-left">
          <h1>{title}</h1>
          {note && <p>{note}</p>}
        </div>
        {actions && <div className="bk-display__actions">{actions}</div>}
      </header>
      <div className="bk-display__body">{children}</div>
    </div>
  );
}

/** Loading, empty, or offline text on a display. */
export function Note({ children }: { children: ReactNode }) {
  return <p className="bk-note">{children}</p>;
}

/** The standard text when a display has no data. */
export function emptyText(online: boolean | null, waiting: string): string {
  if (online === false) return "The watchdog is offline. This page tries again by itself.";
  return waiting;
}

// ---------- the zoom ----------

/** True while a display zooms in. Heavy content waits for the end, so new data does not disturb the animation. */
export const ZoomContext = createContext(false);

export function useZooming(): boolean {
  return useContext(ZoomContext);
}

// ---------- the "i" button ----------

/** A small "i" button. A click shows a note that tells what the panel does. */
export function Info({ title, children }: { title: string; children: ReactNode }) {
  return (
    <Popover.Root>
      <Popover.Trigger className="bk-info" aria-label={`What "${title}" shows`} title="What this panel shows">
        i
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content className="bk-info-note" side="top" align="start" sideOffset={8} collisionPadding={16}>
          <h3>{title}</h3>
          {children}
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}
