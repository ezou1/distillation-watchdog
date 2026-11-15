import { ArrowUpRight, Ban, Flag, Waypoints } from "lucide-react";
import { useCallback, useState } from "react";

import type { AccountSort, AccountState, Summary } from "@/api";
import { AccountView } from "@/components/AccountView";
import { CaseFileButton } from "@/components/CaseFile";
import { RateChart, RateLegend } from "@/components/charts/RateChart";
import { HelpButton, HelpNote, Problem, Quiet, useHelp } from "@/components/Notes";
import { PageBody, PageHeader } from "@/components/PageHeader";
import { Panel, PanelHeader } from "@/components/Panel";
import { ReasonChips } from "@/components/ReasonChips";
import { ScoreCell } from "@/components/Score";
import { Sparkline } from "@/components/Sparkline";
import { StateBadge, YouTag } from "@/components/StateBadge";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useAccounts } from "@/hooks/queries";
import { useListKeys } from "@/hooks/useListKeys";
import type { FeedItem } from "@/hooks/useStream";
import { useApp } from "@/lib/app-context";
import { formatNumber, formatScore, formatSimTime } from "@/lib/format";
import { navigate, routeHref } from "@/lib/router";
import { STATES, STATE_META } from "@/lib/states";
import { cn } from "@/lib/utils";

const SORTS: Array<{ value: AccountSort; label: string }> = [
  { value: "score", label: "Highest score" },
  { value: "requests", label: "Most requests" },
  { value: "last_seen", label: "Last seen" },
];

function StateFilter({
  value,
  counts,
  onChange,
}: {
  value: AccountState | "";
  counts: Partial<Record<AccountState, number>> | undefined;
  onChange: (state: AccountState | "") => void;
}) {
  const options: Array<{ value: AccountState | ""; label: string; count?: number }> = [
    { value: "", label: "All" },
    ...STATES.map((state) => ({ value: state, label: STATE_META[state].label, count: counts?.[state] })),
  ];
  return (
    <div role="group" aria-label="Filter accounts by state" className="flex flex-wrap gap-0.5 rounded-md bg-hover p-0.5">
      {options.map((option) => (
        <button
          key={option.value || "all"}
          type="button"
          aria-pressed={value === option.value}
          onClick={() => onChange(option.value)}
          className={cn(
            "flex h-6 items-center gap-1.5 rounded-[5px] px-2 text-[12px] text-ink-3 transition-colors hover:text-foreground",
            value === option.value && "bg-raised text-foreground shadow-[0_0_0_1px_var(--hairline-strong)]",
          )}
        >
          {option.label}
          {typeof option.count === "number" && (
            <span className="font-mono text-[11px] text-ink-4 tabular-nums">{formatNumber(option.count)}</span>
          )}
        </button>
      ))}
    </div>
  );
}

function plural(count: number, one: string, many: string): string {
  return `${formatNumber(count)} ${count === 1 ? one : many}`;
}

/**
 * The page's one loud moment, in Radar's voice: a two-tone sentence built from live counts,
 * on the grid-and-glow band, with the accounts split by state underneath.
 */
function Overview({ summary, online }: { summary: Summary | null; online: boolean | null }) {
  const counts = summary?.accounts ?? {};
  const total = STATES.reduce((sum, state) => sum + (counts[state] ?? 0), 0);
  const blocked = counts.block ?? 0;
  const review = counts.review ?? 0;
  const requests = (summary?.served ?? 0) + (summary?.blocked ?? 0);

  let lead: string;
  let rest: string;
  if (!summary || total === 0) {
    lead = online === false ? "The watchdog is offline." : "Waiting for traffic.";
    rest =
      online === false
        ? "Start it with scripts/demo.sh, and this page reconnects."
        : "Start normal or attack traffic on the generators, and accounts appear here as requests arrive.";
  } else {
    lead =
      blocked > 0
        ? `${plural(blocked, "account", "accounts")} blocked.`
        : review > 0
          ? `${plural(review, "account", "accounts")} flagged for review.`
          : `${plural(total, "account", "accounts")} watched. None blocked.`;
    const parts = [`The gateway refused ${formatNumber(summary.blocked)} of ${formatNumber(requests)} requests`];
    if (summary.campaigns > 0) parts.push(`linked ${plural(summary.campaigns, "campaign", "campaigns")}`);
    if (blocked > 0 && review > 0) parts.push(`and holds ${formatNumber(review)} more for a person to review`);
    rest = `${parts.join(", ")}.`;
  }

  return (
    <section className="radar-band -mx-4 -mt-4 border-b border-line px-4 pt-10 pb-7 sm:-mx-6 sm:-mt-6 sm:px-6 sm:pt-14">
      <h1 className="display max-w-[42ch] text-[24px] sm:text-[30px]" aria-live="polite">
        <span className="text-foreground">{lead}</span> <span className="text-brand-ink/75">{rest}</span>
      </h1>

      {total > 0 && (
        <div className="mt-8 max-w-3xl">
          <div className="flex h-2 gap-0.5 overflow-hidden rounded-full" role="img" aria-label="Accounts by state">
            {STATES.map((state) => {
              const count = counts[state] ?? 0;
              if (!count) return null;
              return (
                <span
                  key={state}
                  className={cn("h-full first:rounded-l-full last:rounded-r-full", STATE_META[state].dot)}
                  style={{ flexGrow: count }}
                />
              );
            })}
          </div>
          <ul className="mt-3 flex flex-wrap gap-x-5 gap-y-1.5">
            {STATES.map((state) => (
              <li key={state} className="flex items-center gap-2 text-[12.5px]" title={STATE_META[state].meaning}>
                <span className={cn("size-2 rounded-[3px]", STATE_META[state].dot)} aria-hidden="true" />
                <span className="text-ink-3">{STATE_META[state].label}</span>
                <span className="font-mono tabular-nums">{formatNumber(counts[state] ?? 0)}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

const FEED_ICON = {
  block: { icon: Ban, className: "text-st-block-ink" },
  flag: { icon: Flag, className: "text-st-review-ink" },
  campaign: { icon: Waypoints, className: "text-brand-ink" },
} as const;

/** One entry of the activity timeline, in Linear's "who did what, when" form. */
function FeedRow({ item, onAccount }: { item: FeedItem; onAccount: (id: string) => void }) {
  const { icon: Icon, className: iconClass } = FEED_ICON[item.kind];
  const time = <span className="text-ink-4 tabular-nums">{formatSimTime(item.simTime)}</span>;
  const dot = (
    <span className="relative z-10 mt-px flex size-5 shrink-0 items-center justify-center rounded-full bg-panel ring-1 ring-line-strong">
      <Icon className={cn("size-3", iconClass)} aria-hidden="true" />
    </span>
  );
  const rowClass = "flex w-full items-start gap-3 rounded-md px-2 py-2 text-left text-[12.5px] hover:bg-hover";

  if (item.kind === "campaign") {
    return (
      <li className="feed-in">
        <button type="button" onClick={() => navigate({ page: "campaigns", id: item.event.id })} className={rowClass}>
          {dot}
          <span className="min-w-0 leading-5 text-ink-3">
            <span className="font-mono text-foreground">{item.event.id}</span> linked{" "}
            {plural(item.event.size, "account", "accounts")} · score {formatNumber(item.event.score)} · {time}
          </span>
        </button>
      </li>
    );
  }
  const verb = item.kind === "block" ? "blocked" : `set to ${STATE_META[item.event.state]?.label.toLowerCase() ?? "review"}`;
  return (
    <li className="feed-in">
      <button type="button" onClick={() => onAccount(item.event.account_id)} className={rowClass}>
        {dot}
        <span className="min-w-0 leading-5">
          <span className="text-ink-3">
            <span className="font-mono text-foreground">{item.event.pseudonym}</span> {verb} · score{" "}
            {formatScore(item.event.score)} · {time}
          </span>
          {item.event.reason && <span className="block truncate text-[12px] text-ink-4">{item.event.reason}</span>}
        </span>
      </button>
    </li>
  );
}

export function Live() {
  const { stream } = useApp();
  const [helpOpen, toggleHelp] = useHelp("live");
  const [state, setState] = useState<AccountState | "">("");
  const [sort, setSort] = useState<AccountSort>("score");
  const [peek, setPeek] = useState<string | null>(null);

  const accounts = useAccounts({ state, sort, limit: 50 });
  const rows = accounts.data ?? [];
  const openRow = useCallback((index: number) => setPeek(rows[index]?.id ?? null), [rows]);
  const [active, setActive] = useListKeys(rows.length, openRow);

  const history = stream.summary?.rate_history ?? [];
  const peekRow = rows.find((row) => row.id === peek);

  return (
    <>
      <PageHeader title="Live" actions={<HelpButton open={helpOpen} onToggle={toggleHelp} />} />
      <PageBody>
        <Overview summary={stream.summary} online={stream.online} />

        <HelpNote open={helpOpen} onClose={toggleHelp}>
          The chart shows the requests in each interval, served and blocked. Each table row is one account. The score
          goes from 0 to 1, and a high score means more signs of distillation. Select a row to examine the account. Use{" "}
          <kbd className="font-mono">J</kbd> and <kbd className="font-mono">K</kbd> to move through the rows and{" "}
          <kbd className="font-mono">Enter</kbd> to open one.
        </HelpNote>

        <div className="grid gap-5 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
          <Panel>
            <PanelHeader title="Request rate" detail="requests in each interval" actions={<RateLegend />} />
            <div className="h-64 p-4">
              {history.length ? (
                <RateChart history={history} />
              ) : (
                <Quiet className="px-1 py-1">
                  {stream.online === false ? "The watchdog is offline." : "No requests yet. Start a traffic generator."}
                </Quiet>
              )}
            </div>
          </Panel>

          <Panel className="flex min-h-0 flex-col">
            <PanelHeader title="Activity" detail="flags, blocks and campaigns, newest first" />
            {stream.feed.length ? (
              <ol
                className="relative h-64 overflow-y-auto px-3 py-2 before:absolute before:top-4 before:bottom-4 before:left-[21.5px] before:w-px before:bg-line"
                aria-live="polite"
              >
                {stream.feed.map((item) => (
                  <FeedRow key={item.key} item={item} onAccount={setPeek} />
                ))}
              </ol>
            ) : (
              <Quiet>No flags yet. A new flag, block or campaign appears here when the watchdog decides it.</Quiet>
            )}
          </Panel>
        </div>

        <Panel>
          <PanelHeader
            title="Accounts"
            detail={rows.length === 50 ? "first 50" : `${rows.length}`}
            actions={
              <>
                <StateFilter value={state} counts={stream.summary?.accounts} onChange={setState} />
                <Select value={sort} onValueChange={(value) => setSort(value as AccountSort)}>
                  <SelectTrigger size="sm" className="h-7 gap-1.5 border-line bg-transparent px-2 text-[12px]" aria-label="Sort accounts">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent align="end">
                    {SORTS.map((option) => (
                      <SelectItem key={option.value} value={option.value} className="text-[12.5px]">
                        {option.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </>
            }
          />
          {accounts.isError && !rows.length ? (
            <div className="p-4">
              <Problem>The account list did not load. {accounts.error.message}</Problem>
            </div>
          ) : accounts.isPending ? (
            <Quiet>Loading the accounts.</Quiet>
          ) : rows.length === 0 ? (
            <Quiet>
              {state ? "No account is in this state now." : "No accounts yet. They appear when requests reach the gateway."}
            </Quiet>
          ) : (
            <Table>
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  {["Account", "State", "Score", "Score history", "Requests", "Campaign", "Top reasons"].map((name) => (
                    <TableHead
                      key={name}
                      className={cn(
                        "h-9 text-[12px] font-medium text-ink-3 first:pl-5 last:pr-5",
                        name === "Requests" && "text-right",
                      )}
                    >
                      {name}
                    </TableHead>
                  ))}
                </TableRow>
              </TableHeader>
              <TableBody>
                {rows.map((row, index) => {
                  const selected = row.id === peek || index === active;
                  return (
                    <TableRow
                      key={row.id}
                      data-row-index={index}
                      aria-selected={selected}
                      onClick={() => {
                        setActive(index);
                        setPeek(row.id);
                      }}
                      className={cn(
                        "cursor-pointer hover:bg-hover",
                        selected && "bg-hover shadow-[inset_2px_0_0_var(--brand)]",
                      )}
                    >
                      <TableCell className="py-2.5 pl-5">
                        <span className="flex items-center gap-2">
                          <a
                            href={routeHref({ page: "account", id: row.id })}
                            onClick={(event) => event.stopPropagation()}
                            className="font-mono text-[12.5px] underline-offset-2 hover:underline"
                          >
                            {row.pseudonym}
                          </a>
                          {row.you && <YouTag />}
                        </span>
                      </TableCell>
                      <TableCell className="py-2.5">
                        <StateBadge state={row.state} />
                      </TableCell>
                      <TableCell className="py-2.5">
                        <ScoreCell score={row.score} />
                      </TableCell>
                      <TableCell className="py-2.5">
                        <Sparkline history={row.history} state={row.state} />
                      </TableCell>
                      <TableCell className="py-2.5 text-right font-mono text-[12px] tabular-nums">
                        {formatNumber(row.requests)}
                      </TableCell>
                      <TableCell className="py-2.5">
                        {row.campaign_id ? (
                          <a
                            href={routeHref({ page: "campaigns", id: row.campaign_id })}
                            onClick={(event) => event.stopPropagation()}
                            className="font-mono text-[12px] text-brand-ink underline-offset-2 hover:underline"
                          >
                            {row.campaign_id}
                          </a>
                        ) : (
                          <span className="text-ink-4">–</span>
                        )}
                      </TableCell>
                      <TableCell className="py-2.5 pr-5">
                        <ReasonChips reasons={row.reasons} />
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          )}
        </Panel>
      </PageBody>

      <Sheet open={peek !== null} onOpenChange={(open) => !open && setPeek(null)}>
        <SheetContent className="w-full gap-0 overflow-y-auto border-line-strong bg-background p-0 sm:max-w-2xl">
          <SheetHeader className="sticky top-0 z-10 flex-row flex-wrap items-center gap-2 border-b border-line bg-background px-4 py-2.5 pr-12">
            <SheetTitle className="text-[13px]">Account</SheetTitle>
            <SheetDescription className="sr-only">
              A short view of account {peekRow?.pseudonym ?? ""}. Open the full page for all features and evidence.
            </SheetDescription>
            {peek && (
              <div className="ml-auto flex items-center gap-2">
                <CaseFileButton kind="account" id={peek} />
                <Button asChild variant="outline" size="sm" className="rounded-full border-line-strong bg-transparent">
                  <a href={routeHref({ page: "account", id: peek })}>
                    Open full page
                    <ArrowUpRight />
                  </a>
                </Button>
              </div>
            )}
          </SheetHeader>
          {peek && <AccountView id={peek} peek />}
        </SheetContent>
      </Sheet>
    </>
  );
}
