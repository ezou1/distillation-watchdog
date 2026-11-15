import type { AccountState } from "@/api";

/** Display order: most severe first. */
export const STATES: readonly AccountState[] = ["block", "review", "normal", "disclosed", "insufficient"];

interface StateMeta {
  label: string;
  /** What the state means for a person who reads the dashboard. */
  meaning: string;
  /** CSS variable of the fill color (charts, graph nodes, dots). */
  fillVar: string;
  dot: string;
  ink: string;
  badge: string;
}

// Class names are written out in full so Tailwind finds them.
export const STATE_META: Record<AccountState, StateMeta> = {
  block: {
    label: "Blocked",
    meaning: "The gateway refuses this account.",
    fillVar: "--st-block",
    dot: "bg-st-block",
    ink: "text-st-block-ink",
    badge: "bg-st-block/15 text-st-block-ink",
  },
  review: {
    label: "Suspicious",
    meaning: "Flagged. A person must examine it before a block.",
    fillVar: "--st-review",
    dot: "bg-st-review",
    ink: "text-st-review-ink",
    badge: "bg-st-review/15 text-st-review-ink",
  },
  normal: {
    label: "OK",
    meaning: "No signs of distillation.",
    fillVar: "--st-normal",
    dot: "bg-st-normal",
    ink: "text-st-normal-ink",
    badge: "bg-st-normal/15 text-st-normal-ink",
  },
  disclosed: {
    label: "Declared",
    meaning: "The account declared its use, for example a benchmark.",
    fillVar: "--st-disclosed",
    dot: "bg-st-disclosed",
    ink: "text-st-disclosed-ink",
    badge: "bg-st-disclosed/15 text-st-disclosed-ink",
  },
  insufficient: {
    label: "Too few requests",
    meaning: "Not enough requests to give a score.",
    fillVar: "--st-insufficient",
    dot: "bg-st-insufficient",
    ink: "text-st-insufficient-ink",
    badge: "bg-st-insufficient/15 text-st-insufficient-ink",
  },
};

export function stateMeta(state: string | null | undefined): StateMeta {
  return STATE_META[(state ?? "") as AccountState] ?? STATE_META.insufficient;
}

interface DetectorMeta {
  code: string;
  name: string;
  /** The signal in plain words. */
  signal: string;
}

const DETECTORS: Record<string, DetectorMeta> = {
  d1: { code: "D1", name: "Redundancy", signal: "The same conversation state is sent many times." },
  d2: { code: "D2", name: "Engagement", signal: "The account does not read the answers." },
  d3: { code: "D3", name: "Coverage", signal: "Many repositories and topics, not one project." },
  d4: { code: "D4", name: "Elicitation", signal: "Prompts and parameters that ask for full reasoning." },
  d5: { code: "D5", name: "Templates", signal: "First prompts come from a small set of templates." },
  d6: { code: "D6", name: "Environment", signal: "Tool outputs contradict each other or look generated." },
  d7: { code: "D7", name: "Volume", signal: "Request rate, active hours and concurrent sessions." },
  campaign: { code: "CMP", name: "Campaign link", signal: "Links between this account and other accounts." },
};

const UNKNOWN_DETECTOR: DetectorMeta = { code: "–", name: "Other", signal: "" };

/** "d1_redundancy", "d1_dup_rate" and "campaign" all resolve to their detector. */
export function detectorKey(name: string | null | undefined): string {
  const text = (name ?? "").toLowerCase();
  const match = /^d([1-7])(?:_|$)/.exec(text);
  if (match) return `d${match[1]}`;
  // Campaign features have the prefix "c_", for example "c_shared_task_frac".
  return text.startsWith("campaign") || text.startsWith("c_") ? "campaign" : "other";
}

export function detectorMeta(name: string | null | undefined): DetectorMeta {
  return DETECTORS[detectorKey(name)] ?? UNKNOWN_DETECTOR;
}

export const DETECTOR_ORDER = ["d1", "d2", "d3", "d4", "d5", "d6", "d7", "campaign", "other"] as const;

/** "d1_dup_rate_hot +2.1" -> { feature: "d1_dup_rate_hot", value: 2.1 } */
export function parseReason(reason: string): { feature: string; value: number | null } {
  const match = /^(\S+)\s+([+-]?\d+(?:\.\d+)?)$/.exec(reason.trim());
  if (!match) return { feature: reason, value: null };
  return { feature: match[1] ?? reason, value: Number(match[2]) };
}

/** Feature names of the watchdog, in plain words. The name itself stays visible as a tooltip. */
const FEATURE_WORDS: Record<string, string> = {
  d1_dup_rate: "Share of repeated requests",
  d1_hot_dup_rate: "Repeated requests at a high temperature",
  d1_cross_account_dup_rate: "Requests that other accounts also sent",
  d1_max_dup: "Most copies of one request",
  d1_branch_factor: "Different answers asked for one context",
  d1_branch_frac: "Share of contexts asked more than one time",
  d2_correction_rate: "How often the user corrects the model",
  d2_followup_rate: "How often the user continues the conversation",
  d2_return_rate: "How often the user comes back to a session",
  d2_stop_end_frac: "Sessions that end when the model stops",
  d2_user_turns_per_session: "User messages in each session",
  d3_distinct_repos: "Number of different projects",
  d3_repo_entropy: "Spread across projects",
  d3_sessions_per_repo: "Sessions for each project",
  d3_topic_entropy: "Spread across topics",
  d4_elicit_mean: "Prompts that ask for full reasoning (mean)",
  d4_elicit_max: "Prompts that ask for full reasoning (highest)",
  d4_elicit_frac: "Share of prompts that ask for full reasoning",
  d4_max_tokens_frac: "Share of requests at the maximum length",
  d4_max_effort_frac: "Share of requests at the maximum reasoning effort",
  d4_n_gt1_rate: "Share of requests that ask for more than one answer",
  d4_mean_temp: "Mean temperature",
  d6_fake_prob_mean: "Tool outputs that look generated (mean)",
  d6_fake_high_frac: "Share of tool outputs that look generated",
  d7_active_hours_per_day: "Active hours each day",
  d7_distinct_users: "Number of different end users",
  d7_max_concurrent_sessions: "Most sessions at one time",
  d7_mean_concurrent_sessions: "Mean sessions at one time",
  d7_req_per_active_hour: "Requests in each active hour",
  d7_user_field_frac: "Share of requests that name an end user",
};

export function featureWords(name: string): string {
  return FEATURE_WORDS[name] ?? name.replace(/^[dc]\d?_/, "").replace(/_/g, " ");
}
