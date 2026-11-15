// Typed client for the HTTP API in docs/API.md. It uses only the endpoints of that document:
// section 4 (watchdog) and section 3 (agent chat). Do not add an endpoint here without adding it there.

import { agentPath, generatorPath, type GeneratorKind } from "@/lib/origin";

// ---------- shared types ----------

export type AccountState = "insufficient" | "normal" | "review" | "block" | "disclosed";

export interface RatePoint {
  t: number;
  served: number;
  blocked: number;
}

export interface Summary {
  sim_time: number;
  rps: number;
  served: number;
  blocked: number;
  accounts: Partial<Record<AccountState, number>>;
  campaigns: number;
  queue_depth: number;
  dropped: number;
  rate_history: RatePoint[];
}

/** [simulation time, score] */
export type ScorePoint = [number, number];

export interface AccountRow {
  id: string;
  pseudonym: string;
  you: boolean;
  score: number | null;
  state: AccountState;
  requests: number;
  campaign_id: string | null;
  reasons: string[];
  history: ScorePoint[];
}

export interface Feature {
  name: string;
  value: number;
  percentile: number | null;
}

export interface Contribution {
  name: string;
  value: number;
}

export interface Evidence {
  id: string;
  detector?: string;
  account_id?: string;
  request_ids?: string[];
  message_index?: number | null;
  summary?: string;
  weight?: number;
  sim_time?: number;
}

export interface AccountDetail extends Omit<AccountRow, "reasons"> {
  declared_use: string | null;
  features: Feature[];
  contributions: Contribution[];
  evidence: Evidence[];
}

export interface CanonicalMessage {
  index?: number;
  role: string;
  text: string | null;
  tool: string | null;
  args: Record<string, unknown> | null;
}

export interface EvidenceRequest {
  request_id?: string;
  account_id?: string;
  pseudonym?: string;
  sim_time?: number;
  temperature?: number | null;
  n?: number | null;
  max_tokens?: number | null;
  tools_schema_id?: string | null;
  messages?: CanonicalMessage[];
}

export interface EvidenceDetail {
  evidence: Evidence;
  request: EvidenceRequest | null;
  message: CanonicalMessage | null;
}

export interface CampaignRow {
  id: string;
  size: number;
  score: number;
  state: AccountState;
  mean_member_score?: number;
  mean_edge_weight?: number;
  top_edge_types?: string[];
}

export interface CampaignMember {
  id: string;
  pseudonym: string;
  score: number | null;
  state: AccountState;
}

export interface CampaignEdge {
  source: string;
  target: string;
  weight: number;
  types?: Record<string, number>;
  evidence?: string;
}

export interface CampaignDetail extends CampaignRow {
  members: CampaignMember[];
  edges: CampaignEdge[];
  template: string | null;
  evidence: Evidence[];
}

export interface Casefile {
  id: string;
  status: string;
  cached: boolean;
  text: string;
  cited: string[];
  evidence: Evidence[];
  reason?: string;
}

export interface GraphNode {
  id: string;
  pseudonym: string;
  score: number | null;
  state: AccountState;
  campaign_id: string | null;
  you: boolean;
}

export interface GraphEdge {
  source: string;
  target: string;
  weight: number;
  types?: Record<string, number>;
}

export interface Graph {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

/** The report files, as the offline evaluation wrote them. The shape belongs to dw.eval, so it is read defensively. */
export interface EvalReport {
  metrics?: unknown;
  controls?: unknown;
  compute?: unknown;
  load?: unknown;
  images?: string[];
  [section: string]: unknown;
}

export interface FlagEvent {
  account_id: string;
  pseudonym: string;
  state: AccountState;
  score: number | null;
  reason?: string;
}

export interface CampaignEvent {
  id: string;
  size: number;
  score: number;
  state: AccountState;
}

export type AccountSort = "score" | "requests" | "last_seen";

// ---------- agent chat types ----------

export interface AgentToolCall {
  id: string;
  name: string;
  arguments: Record<string, unknown>;
}

export interface AgentMessage {
  role: "user" | "assistant" | "tool" | string;
  content: string | null;
  tool_calls?: AgentToolCall[];
  tool_call_id?: string;
  name?: string;
}

export type AgentStatus = "idle" | "done" | "awaiting_approval" | "step_limit" | "error";

export interface AgentSession {
  status: AgentStatus | string;
  steps?: number;
  messages: AgentMessage[];
  pending?: { approval_id: string; command: string } | null;
  error?: string;
}

export interface SandboxFile {
  path: string;
  size: number;
}

// ---------- transport ----------

export class ApiError extends Error {
  /** HTTP status, or undefined when the server did not answer. */
  readonly status?: number;

  constructor(message: string, status?: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

interface RequestOptions {
  method?: "GET" | "POST";
  body?: unknown;
  timeoutMs?: number;
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), options.timeoutMs ?? 8000);
  const headers: Record<string, string> = { Accept: "application/json" };
  if (options.body !== undefined) headers["Content-Type"] = "application/json";
  let response: Response;
  try {
    response = await fetch(path, {
      method: options.method ?? "GET",
      headers,
      body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
      signal: controller.signal,
    });
  } catch {
    throw new ApiError(controller.signal.aborted ? "The server did not answer in time." : "The server is not reachable.");
  } finally {
    clearTimeout(timer);
  }
  if (!response.ok) throw new ApiError(`The server answered ${response.status}.`, response.status);
  return (await response.json()) as T;
}

const enc = encodeURIComponent;
const CASEFILE_TIMEOUT_MS = 120_000;

// ---------- watchdog (docs/API.md section 4) ----------

export const watchdog = {
  streamUrl: "/api/stream",

  summary: () => request<Summary>("/api/summary"),

  accounts: (params: { state?: AccountState | ""; sort?: AccountSort; limit?: number } = {}) => {
    // The query is built first so the path stays a plain literal that tests/test_app_frontend.py can read.
    const query = `state=${enc(params.state ?? "")}&sort=${enc(params.sort ?? "score")}&limit=${params.limit ?? 50}`;
    return request<{ accounts: AccountRow[] }>(`/api/accounts?${query}`);
  },

  account: (id: string) => request<AccountDetail>(`/api/accounts/${enc(id)}`),

  accountCasefile: (id: string) =>
    request<Casefile>(`/api/accounts/${enc(id)}/casefile`, { method: "POST", timeoutMs: CASEFILE_TIMEOUT_MS }),

  evidence: (id: string) => request<EvidenceDetail>(`/api/evidence/${enc(id)}`),

  campaigns: () => request<{ campaigns: CampaignRow[] }>("/api/campaigns"),

  campaign: (id: string) => request<CampaignDetail>(`/api/campaigns/${enc(id)}`),

  campaignCasefile: (id: string) =>
    request<Casefile>(`/api/campaigns/${enc(id)}/casefile`, { method: "POST", timeoutMs: CASEFILE_TIMEOUT_MS }),

  graph: () => request<Graph>("/api/graph"),

  evaluation: () => request<EvalReport>("/api/eval", { timeoutMs: 20_000 }),
};

// ---------- agent chat (docs/API.md section 3) ----------

// One agent turn can run several model calls, so it gets a long timeout.
const AGENT_TURN_TIMEOUT_MS = 300_000;

export const agent = {
  chat: (message: string, tools: boolean) =>
    request<AgentSession>(agentPath("/api/chat"), {
      method: "POST",
      body: { message, tools },
      timeoutMs: AGENT_TURN_TIMEOUT_MS,
    }),

  approve: (approvalId: string, approve: boolean) =>
    request<AgentSession>(agentPath("/api/approve"), {
      method: "POST",
      body: { approval_id: approvalId, approve },
      timeoutMs: AGENT_TURN_TIMEOUT_MS,
    }),

  session: () => request<AgentSession>(agentPath("/api/session")),

  newSession: () => request<{ ok: boolean }>(agentPath("/api/session/new"), { method: "POST", body: {} }),

  files: () => request<{ files: SandboxFile[] }>(agentPath("/api/files")),
};

// ---------- traffic generators (docs/API.md section 2) ----------

export interface PersonaCounters {
  running: boolean;
  accounts: number;
  sent: number;
  served: number;
  /** Refused requests, not accounts. */
  blocked: number;
  /** Complete trajectories received before the accounts were blocked. */
  leaked: number;
  errors?: number;
}

export interface GeneratorStatus {
  role: GeneratorKind;
  scale: string;
  speed: number;
  sim_time: number | null;
  error?: string;
  personas: Record<string, PersonaCounters>;
}

export const generator = (kind: GeneratorKind) => ({
  status: () => request<GeneratorStatus>(generatorPath(kind, "/api/status")),

  start: (persona: string, scale: string, speed: number) =>
    // Building a scenario the first time can take a while on the generator.
    request<{ ok: boolean }>(generatorPath(kind, "/api/start"), {
      method: "POST",
      body: { persona, scale, speed },
      timeoutMs: 120_000,
    }),

  stop: (persona: string) =>
    request<{ ok: boolean }>(generatorPath(kind, "/api/stop"), { method: "POST", body: { persona } }),
});
