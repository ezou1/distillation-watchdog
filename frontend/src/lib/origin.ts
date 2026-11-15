// The same build is served at /app by four servers: the watchdog (dashboard API), the agent chat, and the two
// traffic generators. The agent chat accepts requests only from its own origin, so each page calls only the server
// it came from. Pages of the other servers are links to those servers.

const WATCHDOG_PORT = import.meta.env.VITE_WATCHDOG_PORT ?? "8010";
const AGENT_PORT = import.meta.env.VITE_AGENT_PORT ?? "8003";
const GENERATOR_PORTS = {
  normal: import.meta.env.VITE_NORMAL_GEN_PORT ?? "8001",
  attack: import.meta.env.VITE_ATTACK_GEN_PORT ?? "8002",
} as const;

export type GeneratorKind = keyof typeof GENERATOR_PORTS;

/** "dev": the Vite dev server proxies all APIs. Otherwise the server that serves this page. */
export type Role = "dev" | "watchdog" | "agent" | GeneratorKind;

export const IS_DEV = import.meta.env.DEV;

export function isGenerator(role: Role): role is GeneratorKind {
  return role === "normal" || role === "attack";
}

/** The dashboard pages (Live, Campaigns, Evaluation) need the watchdog API. */
export function hasDashboard(role: Role): boolean {
  return role === "dev" || role === "watchdog";
}

function siteUrl(port: string): string {
  return `${window.location.protocol}//${window.location.hostname}:${port}/app/`;
}

export const watchdogSite = (): string => siteUrl(WATCHDOG_PORT);
export const agentSite = (): string => siteUrl(AGENT_PORT);
export const generatorSite = (kind: GeneratorKind): string => siteUrl(GENERATOR_PORTS[kind]);

/** The agent API is at /api on its own server. In dev the Vite proxy exposes it at /agent-api. */
export function agentPath(path: string): string {
  return IS_DEV ? path.replace(/^\/api/, "/agent-api") : path;
}

/** A generator API is at /api on its own server. In dev the Vite proxy exposes them at /normal-api and /attack-api. */
export function generatorPath(kind: GeneratorKind, path: string): string {
  return IS_DEV ? path.replace(/^\/api/, `/${kind}-api`) : path;
}

async function answers(path: string): Promise<unknown> {
  try {
    const response = await fetch(path, { headers: { Accept: "application/json" } });
    return response.ok ? await response.json() : null;
  } catch {
    return null;
  }
}

/** Find which server serves this page. It tries the likely server first so the console stays clean. */
export async function detectRole(): Promise<Role> {
  if (IS_DEV) return "dev";
  const port = window.location.port;
  type Probe = [path: string, read: (body: unknown) => Role | null];
  const watchdog: Probe = ["/api/summary", () => "watchdog"];
  const agent: Probe = ["/api/session", () => "agent"];
  const generator: Probe = [
    "/api/status",
    (body) => {
      const role = (body as { role?: unknown } | null)?.role;
      return role === "normal" || role === "attack" ? role : null;
    },
  ];
  const probes =
    port === AGENT_PORT
      ? [agent, watchdog, generator]
      : Object.values(GENERATOR_PORTS).includes(port as never)
        ? [generator, watchdog, agent]
        : [watchdog, agent, generator];
  for (const [path, read] of probes) {
    const body = await answers(path);
    const role = body === null ? null : read(body);
    if (role) return role;
  }
  // No server answers: keep the role of the likely server and let the page show "offline".
  const fallback: Record<string, Role> = { [AGENT_PORT]: "agent", [GENERATOR_PORTS.normal]: "normal", [GENERATOR_PORTS.attack]: "attack" };
  return fallback[port] ?? "watchdog";
}
