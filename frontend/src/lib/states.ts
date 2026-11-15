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
    label: "Review",
    meaning: "Flagged. A person must examine it before a block.",
    fillVar: "--st-review",
    dot: "bg-st-review",
    ink: "text-st-review-ink",
    badge: "bg-st-review/15 text-st-review-ink",
  },
  normal: {
    label: "Normal",
    meaning: "No signs of distillation.",
    fillVar: "--st-normal",
    dot: "bg-st-normal",
    ink: "text-st-normal-ink",
    badge: "bg-st-normal/15 text-st-normal-ink",
  },
  disclosed: {
    label: "Disclosed",
    meaning: "The account declared its use, for example a benchmark.",
    fillVar: "--st-disclosed",
    dot: "bg-st-disclosed",
    ink: "text-st-disclosed-ink",
    badge: "bg-st-disclosed/15 text-st-disclosed-ink",
  },
  insufficient: {
    label: "Not scored",
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
