import { useMemo, useSyncExternalStore } from "react";

export type Theme = "dark" | "light";

const STORAGE_KEY = "dw-theme";
const listeners = new Set<() => void>();

function currentTheme(): Theme {
  return document.documentElement.dataset.theme === "light" ? "light" : "dark";
}

export function setTheme(theme: Theme): void {
  document.documentElement.dataset.theme = theme;
  try {
    localStorage.setItem(STORAGE_KEY, theme);
  } catch {
    // Storage can be blocked. The theme still applies for this visit.
  }
  listeners.forEach((listener) => listener());
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function useTheme(): Theme {
  return useSyncExternalStore(subscribe, currentTheme);
}

/**
 * Resolved values of CSS color variables, for libraries that cannot read CSS variables (Cytoscape).
 * The values update when the theme changes.
 */
export function useCssColors<Name extends string>(names: readonly Name[]): Record<Name, string> {
  const theme = useTheme();
  const key = names.join(",");
  return useMemo(() => {
    const style = getComputedStyle(document.documentElement);
    const out = {} as Record<Name, string>;
    for (const name of key.split(",") as Name[]) out[name] = style.getPropertyValue(name).trim();
    return out;
    // `theme` is a dependency on purpose: the variables change value with it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, theme]);
}
