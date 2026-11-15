import { useState, type ReactNode } from "react";

import type { AccountDetail, Feature } from "@/api";
import { CaseFilePanel } from "@/components/CaseFile";
import { ContributionBars } from "@/components/charts/ContributionBars";
import { ScoreHistoryChart } from "@/components/charts/ScoreHistoryChart";
import { EvidenceByDetector } from "@/components/EvidenceList";
import { Problem, Quiet } from "@/components/Notes";
import { Panel, PanelHeader } from "@/components/Panel";
import { RiskBar } from "@/components/Score";
import { StateBadge, YouTag } from "@/components/StateBadge";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useAccount } from "@/hooks/queries";
import { formatNumber, formatScore } from "@/lib/format";
import { routeHref } from "@/lib/router";
import { detectorMeta, stateMeta } from "@/lib/states";
import { cn } from "@/lib/utils";

const FEATURES_SHOWN = 10;
const PEEK_EVIDENCE_PER_DETECTOR = 3;

/** The title block, like a Linear issue: the name, then one plain sentence that says where the account stands. */
function Identity({ account }: { account: AccountDetail }) {
  const scored = typeof account.score === "number";
  return (
    <div className="min-w-0">
      <div className="flex flex-wrap items-center gap-2.5">
        <h1 className="font-mono text-[24px] font-medium tracking-[-0.02em]">{account.pseudonym}</h1>
        {account.you && <YouTag />}
      </div>
      <p className="mt-2 max-w-[68ch] text-[14.5px] leading-relaxed text-ink-2">
        {stateMeta(account.state).meaning}{" "}
        {scored
          ? `The watchdog scored it ${formatScore(account.score)} from ${formatNumber(account.requests)} requests.`
          : `It has sent ${formatNumber(account.requests)} requests so far.`}
      </p>
    </div>
  );
}

/** Radar's risk card: a machine readout, the score large, and the violet-to-magenta scale it sits on. */
function RiskProfile({ account }: { account: AccountDetail }) {
  const scored = typeof account.score === "number";
  return (
    <section className="radar-band overflow-hidden rounded-xl border border-line bg-panel">
      <div className="px-5 pt-4 pb-5">
        <div className="flex items-center justify-between gap-3">
          <span className="readout">{scored ? "Risk profile complete" : "Risk profile pending"}</span>
          <StateBadge state={account.state} />
        </div>
        <div className="display mt-4 text-[44px] leading-none">{formatScore(account.score)}</div>
        <RiskBar score={account.score} className="mt-4 h-2" />
        <div className="mt-1.5 flex justify-between font-mono text-[10.5px] text-ink-4">
          <span>0 low risk</span>
          <span>1 high risk</span>
        </div>
      </div>
    </section>
  );
}

/** Linear's property list: quiet label on the left, the value on the right. */
function Properties({ account }: { account: AccountDetail }) {
  const rows: Array<{ label: string; value: ReactNode }> = [
    { label: "Requests", value: <span className="font-mono tabular-nums">{formatNumber(account.requests)}</span> },
    {
      label: "Campaign",
      value: account.campaign_id ? (
        <a
          href={routeHref({ page: "campaigns", id: account.campaign_id })}
          className="font-mono text-brand-ink underline-offset-2 hover:underline"
        >
          {account.campaign_id}
        </a>
      ) : (
        <span className="text-ink-4">None</span>
      ),
    },
  ];
  if (account.declared_use) rows.push({ label: "Declared use", value: account.declared_use });
  return (
    <dl className="grid gap-px px-1">
      {rows.map((row) => (
        <div key={row.label} className="grid grid-cols-[96px_minmax(0,1fr)] items-center gap-3 py-1.5 text-[13px]">
          <dt className="text-ink-3">{row.label}</dt>
          <dd className="min-w-0 truncate">{row.value}</dd>
        </div>
      ))}
    </dl>
  );
}

function PercentileBar({ percentile }: { percentile: number | null }) {
  if (typeof percentile !== "number") return <span className="text-ink-4">–</span>;
  const value = Math.max(0, Math.min(100, percentile));
  const extreme = value >= 95;
  return (
    <span className="flex items-center gap-2">
      <span className="h-1.5 w-24 overflow-hidden rounded-full bg-hover">
        <span
          className={cn("block h-full rounded-full", extreme ? "bg-foreground" : "bg-ink-4")}
          style={{ width: `${value}%` }}
        />
      </span>
      <span className={cn("font-mono text-[11.5px] tabular-nums", extreme ? "font-medium" : "text-ink-3")}>
        {value.toFixed(1)}
      </span>
    </span>
  );
}

function FeatureTable({ features }: { features: Feature[] }) {
  const [all, setAll] = useState(false);
  // Most unusual first: a feature far from the middle of the population is the interesting one.
  const sorted = [...features].sort(
    (a, b) => Math.abs((b.percentile ?? 50) - 50) - Math.abs((a.percentile ?? 50) - 50),
  );
  const shown = all ? sorted : sorted.slice(0, FEATURES_SHOWN);
  return (
    <>
      <Table>
        <TableHeader>
          <TableRow className="hover:bg-transparent">
            <TableHead className="h-8 pl-4 text-[11.5px] font-medium text-ink-3">Feature</TableHead>
            <TableHead className="h-8 text-[11.5px] font-medium text-ink-3">Detector</TableHead>
            <TableHead className="h-8 text-right text-[11.5px] font-medium text-ink-3">Value</TableHead>
            <TableHead className="h-8 pr-4 text-[11.5px] font-medium text-ink-3">Percentile of all accounts</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {shown.map((feature) => (
            <TableRow key={feature.name} className="hover:bg-hover">
              <TableCell className="py-1.5 pl-4 font-mono text-[11.5px]">{feature.name}</TableCell>
              <TableCell className="py-1.5 text-[12px] text-ink-3">{detectorMeta(feature.name).name}</TableCell>
              <TableCell className="py-1.5 text-right font-mono text-[11.5px] tabular-nums">
                {formatNumber(feature.value)}
              </TableCell>
              <TableCell className="py-1.5 pr-4">
                <PercentileBar percentile={feature.percentile} />
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      {sorted.length > FEATURES_SHOWN && (
        <div className="border-t border-line px-2 py-1.5">
          <Button variant="ghost" size="xs" className="text-ink-2" onClick={() => setAll((was) => !was)}>
            {all ? "Show the most unusual only" : `Show all ${sorted.length} features`}
          </Button>
        </div>
      )}
    </>
  );
}

/**
 * Everything the watchdog knows about one account. `peek` is the short form for the side sheet:
 * one column, no feature table, and a few evidence items for each detector.
 */
export function AccountView({ id, peek = false }: { id: string; peek?: boolean }) {
  const query = useAccount(id);

  if (query.isPending) return <Quiet>Loading the account.</Quiet>;
  if (query.isError && !query.data) {
    return (
      <div className="p-4">
        <Problem>
          {query.error instanceof Error && "status" in query.error && query.error.status === 404
            ? "The watchdog does not know this account now. It can be gone after a reset."
            : `The account did not load. ${query.error.message}`}
        </Problem>
      </div>
    );
  }
  const account = query.data;
  if (!account) return null;

  const contributions = account.contributions ?? [];
  const history = account.history ?? [];
  const evidence = account.evidence ?? [];

  const rail = (
    <aside className={cn("grid content-start gap-5", !peek && "xl:sticky xl:top-0")}>
      <RiskProfile account={account} />
      <Properties account={account} />
    </aside>
  );

  return (
    <div
      className={cn(
        "grid items-start gap-6",
        peek ? "p-5" : "xl:grid-cols-[minmax(0,1fr)_300px]",
      )}
    >
    <div className="grid min-w-0 gap-5">
      <Identity account={account} />
      {peek && rail}

      <div className={cn("grid gap-5", !peek && "2xl:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]")}>
        <Panel>
          <PanelHeader title="What drives the score" detail="contribution to the log-odds, largest first" />
          <div className="p-4">
            {contributions.length ? (
              <ContributionBars contributions={contributions} />
            ) : (
              <Quiet className="px-0 py-1">The account has no score yet, so there are no contributions.</Quiet>
            )}
          </div>
        </Panel>
        <Panel>
          <PanelHeader title="Score over time" detail="simulation clock" />
          <div className="h-56 p-3">
            {history.length ? (
              <ScoreHistoryChart history={history} />
            ) : (
              <Quiet className="px-1 py-1">No score history yet.</Quiet>
            )}
          </div>
        </Panel>
      </div>

      {!peek && (
        <Panel>
          <PanelHeader title="Feature values" detail="most unusual first" />
          {account.features?.length ? (
            <FeatureTable features={account.features} />
          ) : (
            <Quiet>The watchdog has no features for this account yet.</Quiet>
          )}
        </Panel>
      )}

      <Panel>
        <PanelHeader
          title="Evidence"
          detail={`${evidence.length} ${evidence.length === 1 ? "item" : "items"}. Select one to see the exact request.`}
        />
        <div className="px-2 py-3">
          {evidence.length ? (
            <EvidenceByDetector evidence={evidence} limitPerGroup={peek ? PEEK_EVIDENCE_PER_DETECTOR : undefined} />
          ) : (
            <Quiet className="px-2 py-1">No evidence yet. Evidence appears when a detector finds a clear sign.</Quiet>
          )}
        </div>
      </Panel>

      <CaseFilePanel kind="account" id={id} />
    </div>
    {!peek && rail}
    </div>
  );
}
