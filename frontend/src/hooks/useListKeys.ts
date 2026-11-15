import { useEffect, useState } from "react";

function typingOrDialogOpen(target: EventTarget | null): boolean {
  if (document.querySelector('[role="dialog"]')) return true;
  if (!(target instanceof HTMLElement)) return false;
  return target.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName);
}

/**
 * Keyboard movement through a list: J moves down, K moves up, Enter opens the active row.
 * The keys are off while the person types or a dialog is open.
 */
export function useListKeys(count: number, onOpen: (index: number) => void): [number, (index: number) => void] {
  const [active, setActive] = useState(-1);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.metaKey || event.ctrlKey || event.altKey || typingOrDialogOpen(event.target)) return;
      if (event.key === "j") setActive((index) => Math.min(index + 1, count - 1));
      else if (event.key === "k") setActive((index) => Math.max(index - 1, 0));
      else if (event.key === "Enter" && active >= 0 && active < count) {
        // A focused button or link handles Enter by itself.
        if (event.target instanceof HTMLElement && event.target.closest("button, a")) return;
        onOpen(active);
      } else return;
      event.preventDefault();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [count, active, onOpen]);

  useEffect(() => {
    if (active < 0) return;
    document.querySelector(`[data-row-index="${active}"]`)?.scrollIntoView({ block: "nearest" });
  }, [active]);

  return [active, setActive];
}

/** A single key that runs an action, with the same guards as the list keys. */
export function useKey(key: string, action: () => void, enabled = true): void {
  useEffect(() => {
    if (!enabled) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.metaKey || event.ctrlKey || event.altKey || typingOrDialogOpen(event.target)) return;
      if (event.key !== key) return;
      event.preventDefault();
      action();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [key, action, enabled]);
}
