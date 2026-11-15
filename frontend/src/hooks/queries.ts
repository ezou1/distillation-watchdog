import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { agent, watchdog, type AccountSort, type AccountState } from "@/api";

const POLL_ACCOUNTS_MS = 2000;
const POLL_ACCOUNT_MS = 3000;
const POLL_CAMPAIGNS_MS = 4000;

export function useAccounts(params: { state?: AccountState | ""; sort?: AccountSort; limit?: number }, enabled = true) {
  const { state = "", sort = "score", limit = 50 } = params;
  return useQuery({
    queryKey: ["accounts", state, sort, limit],
    queryFn: () => watchdog.accounts({ state, sort, limit }),
    select: (data) => data.accounts ?? [],
    refetchInterval: POLL_ACCOUNTS_MS,
    placeholderData: keepPreviousData,
    enabled,
  });
}

export function useAccount(id: string | null | undefined) {
  return useQuery({
    queryKey: ["account", id],
    queryFn: () => watchdog.account(id ?? ""),
    refetchInterval: POLL_ACCOUNT_MS,
    enabled: Boolean(id),
  });
}

export function useCampaigns(enabled = true) {
  return useQuery({
    queryKey: ["campaigns"],
    queryFn: watchdog.campaigns,
    select: (data) => data.campaigns ?? [],
    refetchInterval: POLL_CAMPAIGNS_MS,
    enabled,
  });
}

export function useCampaign(id: string | null | undefined) {
  return useQuery({
    queryKey: ["campaign", id],
    queryFn: () => watchdog.campaign(id ?? ""),
    refetchInterval: POLL_CAMPAIGNS_MS,
    enabled: Boolean(id),
  });
}

export function useGraph() {
  return useQuery({
    queryKey: ["graph"],
    queryFn: watchdog.graph,
    refetchInterval: POLL_CAMPAIGNS_MS,
  });
}

export function useEvidence(id: string | null) {
  return useQuery({
    queryKey: ["evidence", id],
    queryFn: () => watchdog.evidence(id ?? ""),
    enabled: Boolean(id),
    staleTime: Infinity,
  });
}

export function useEvaluation() {
  return useQuery({
    queryKey: ["eval"],
    queryFn: watchdog.evaluation,
    staleTime: Infinity,
  });
}

/**
 * A case file is written on request only (it costs an LLM call), so the query does not run by itself:
 * call `refetch()` from the button. The result stays in the cache while the person moves between pages.
 */
export function useCasefile(kind: "account" | "campaign", id: string) {
  return useQuery({
    queryKey: ["casefile", kind, id],
    queryFn: () => (kind === "account" ? watchdog.accountCasefile(id) : watchdog.campaignCasefile(id)),
    enabled: false,
    staleTime: Infinity,
    gcTime: Infinity,
  });
}

export function useAgentSession(enabled: boolean) {
  return useQuery({
    queryKey: ["agent", "session"],
    queryFn: agent.session,
    enabled,
    staleTime: Infinity,
  });
}

export function useAgentFiles(enabled: boolean) {
  return useQuery({
    queryKey: ["agent", "files"],
    queryFn: agent.files,
    select: (data) => data.files ?? [],
    enabled,
  });
}
