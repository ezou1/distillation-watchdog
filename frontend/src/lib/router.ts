import { useMemo, useSyncExternalStore } from "react";

import type { GeneratorKind } from "@/lib/origin";

// Hash routing: the app is served as static files under /app, so the path must not change.

export type Route =
  | { page: "live" }
  | { page: "account"; id: string }
  | { page: "campaigns"; id?: string }
  | { page: "eval" }
  | { page: "traffic"; kind: GeneratorKind }
  | { page: "agent" };

export function parseRoute(hash: string): Route {
  const parts = hash.replace(/^#\/?/, "").split("/").filter(Boolean).map(decodeURIComponent);
  const [page, id] = parts;
  if (page === "accounts" && id) return { page: "account", id };
  if (page === "campaigns") return id ? { page: "campaigns", id } : { page: "campaigns" };
  if (page === "eval") return { page: "eval" };
  if (page === "traffic") return { page: "traffic", kind: id === "attack" ? "attack" : "normal" };
  if (page === "agent") return { page: "agent" };
  return { page: "live" };
}

export function routeHref(route: Route): string {
  switch (route.page) {
    case "account":
      return `#/accounts/${encodeURIComponent(route.id)}`;
    case "campaigns":
      return route.id ? `#/campaigns/${encodeURIComponent(route.id)}` : "#/campaigns";
    case "eval":
      return "#/eval";
    case "traffic":
      return `#/traffic/${route.kind}`;
    case "agent":
      return "#/agent";
    default:
      return "#/live";
  }
}

export function navigate(route: Route): void {
  window.location.hash = routeHref(route);
}

function subscribe(onChange: () => void): () => void {
  window.addEventListener("hashchange", onChange);
  return () => window.removeEventListener("hashchange", onChange);
}

export function useRoute(): Route {
  const hash = useSyncExternalStore(subscribe, () => window.location.hash);
  return useMemo(() => parseRoute(hash), [hash]);
}
