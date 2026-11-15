import { ArrowUpRight, X } from "lucide-react";
import { useMemo, useState } from "react";

import type { CampaignDetail, CampaignRow, Graph, GraphEdge, GraphNode } from "@/api";
import { CampaignGraph, type GraphSelection } from "@/components/CampaignGraph";
import { CaseFileButton, CaseFilePanel } from "@/components/CaseFile";
import { EvidenceList } from "@/components/EvidenceList";
import { HelpButton, HelpNote, Problem, Quiet, useHelp } from "@/components/Notes";
import { PageBody, PageHeader } from "@/components/PageHeader";
import { Panel, PanelHeader, SectionLabel } from "@/components/Panel";
import { RiskBar, ScoreCell } from "@/components/Score";
import { StateBadge, YouTag } from "@/components/StateBadge";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useCampaign, useCampaigns, useGraph } from "@/hooks/queries";
import { formatNumber, formatScore, humanizeKey } from "@/lib/format";
import { navigate, routeHref } from "@/lib/router";
import { STATES, STATE_META } from "@/lib/states";
import { cn } from "@/lib/utils";

const LINKS_SHOWN = 200;
const NEIGHBORS_SHOWN = 8;

const LINK_TYPE_LABEL: Record<string, string> = {
  shared_task: "Shared tasks",
  sync: "Synchronized starts",
  shared_skeleton: "Same prompt template",
  shared_files: "Same files",
};

function linkTypeLabel(type: string): string {
  return LINK_TYPE_LABEL[type] ?? humanizeKey(type);
}

const EMPTY_GRAPH: Graph = { nodes: [], edges: [] };

function sameLink(edge: { source: string; target: string }, a: string, b: string): boolean {
  return (edge.source === a && edge.target === b) || (edge.source === b && edge.target === a);
}

// ---------- left: the list of campaigns ----------

function CampaignList({ campaigns, selected }: { campaigns: CampaignRow[]; selected: string | null }) {
  return (
    <ul className="grid gap-px p-1.5">
      {campaigns.map((campaign) => {
        const active = campaign.id === selected;
        return (
          <li key={campaign.id} className="min-w-0">
            <a
              href={routeHref({ page: "campaigns", id: campaign.id })}
              aria-current={active ? "true" : undefined}
              className={cn(
                "block rounded-md px-2.5 py-2 transition-colors hover:bg-hover",
                active && "bg-hover shadow-[inset_2px_0_0_var(--brand)]",
              )}
            >
              <span className="flex items-center gap-2">
                <span className="font-mono text-[12.5px] font-medium">{campaign.id}</span>
                <StateBadge state={campaign.state} />
              </span>
              <span className="mt-1 flex flex-wrap gap-x-3 text-[12px] text-ink-3">
                <span>{formatNumber(campaign.size)} accounts</span>
                <span>score {formatNumber(campaign.score)}</span>
              </span>
              {(campaign.top_edge_types?.length ?? 0) > 0 && (
                <span className="mt-0.5 block truncate text-[12px] text-ink-4">
                  {campaign.top_edge_types?.map(linkTypeLabel).join(", ")}
                </span>
              )}
            </a>
          </li>
        );
      })}
    </ul>
  );
}

// ---------- right: the inspector ----------

function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-1">
      <dt className="text-[12px] text-ink-3">{label}</dt>
      <dd className="text-right font-mono text-[12px] tabular-nums">{children}</dd>
    </div>
  );
}

function NodeInspector({
  node,
  graph,
  names,
  onSelect,
}: {
  node: GraphNode;
  graph: Graph;
  names: Map<string, string>;
  onSelect: (selection: GraphSelection) => void;
}) {
  const links = graph.edges
    .filter((edge) => edge.source === node.id || edge.target === node.id)
    .sort((a, b) => b.weight - a.weight);
  return (
    <div className="grid gap-4 p-4">
      <div>
        <SectionLabel>Account</SectionLabel>
        <div className="mt-1 flex flex-wrap items-center gap-2">
          <span className="font-mono text-[15px] font-medium">{node.pseudonym}</span>
          {node.you && <YouTag />}
        </div>
        <div className="mt-2 flex items-center gap-3">
          <StateBadge state={node.state} />
          <span className="font-mono text-[12.5px] tabular-nums">{formatScore(node.score)}</span>
        </div>
        <RiskBar score={node.score} className="mt-2" />
      </div>
      <div className="flex flex-wrap gap-2">
        <Button asChild size="sm" className="rounded-full">
          <a href={routeHref({ page: "account", id: node.id })}>
            Open account
            <ArrowUpRight />
          </a>
        </Button>
        {node.campaign_id && (
          <Button asChild variant="outline" size="sm" className="rounded-full border-line-strong bg-transparent">
            <a href={routeHref({ page: "campaigns", id: node.campaign_id })}>Show {node.campaign_id}</a>
          </Button>
        )}
      </div>
      <div>
        <SectionLabel>Strongest links ({links.length})</SectionLabel>
        {links.length === 0 ? (
          <p className="mt-1 text-[12px] text-ink-3">This account has no link to another account.</p>
        ) : (
          <ul className="mt-1 grid gap-px">
            {links.slice(0, NEIGHBORS_SHOWN).map((edge) => {
              const other = edge.source === node.id ? edge.target : edge.source;
              return (
                <li key={`${edge.source}-${edge.target}`}>
                  <button
                    type="button"
                    onClick={() => onSelect({ kind: "edge", source: edge.source, target: edge.target })}
                    className="flex w-full items-baseline justify-between gap-3 rounded-md px-2 py-1 text-left hover:bg-hover"
                  >
                    <span className="font-mono text-[12px]">{names.get(other) ?? "account"}</span>
                    <span className="font-mono text-[11.5px] text-ink-3 tabular-nums">weight {formatNumber(edge.weight)}</span>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </div>
  );
}

function EdgeInspector({
  edge,
  evidence,
  names,
  onSelect,
}: {
  edge: GraphEdge;
  /** The sentence of the watchdog for this link. It comes with the campaign detail. */
  evidence: string | undefined;
  names: Map<string, string>;
  onSelect: (selection: GraphSelection) => void;
}) {
  const types = Object.entries(edge.types ?? {}).sort((a, b) => b[1] - a[1]);
  const scale = Math.max(...types.map(([, value]) => value), 0.0001);
  return (
    <div className="grid gap-4 p-4">
      <div>
        <SectionLabel>Link between two accounts</SectionLabel>
        <div className="mt-1 grid gap-1">
          {[edge.source, edge.target].map((id) => (
            <button
              key={id}
              type="button"
              onClick={() => onSelect({ kind: "node", id })}
              className="w-fit rounded-sm font-mono text-[14px] font-medium underline-offset-2 hover:underline"
            >
              {names.get(id) ?? "account"}
            </button>
          ))}
        </div>
      </div>
      <dl>
        <Fact label="Weight">{formatNumber(edge.weight)}</Fact>
      </dl>
      <div>
        <SectionLabel>Why they are linked</SectionLabel>
        {types.length === 0 ? (
          <p className="mt-1 text-[12px] text-ink-3">The watchdog gave no link type.</p>
        ) : (
          <ul className="mt-2 grid gap-2">
            {types.map(([type, value]) => (
              <li key={type}>
                <div className="flex items-baseline justify-between gap-3 text-[12px]">
                  <span>{linkTypeLabel(type)}</span>
                  <span className="font-mono text-ink-3 tabular-nums">{formatNumber(value)}</span>
                </div>
                <div className="mt-1 h-1 overflow-hidden rounded-full bg-hover">
                  <div className="h-full rounded-full bg-ink-3" style={{ width: `${(value / scale) * 100}%` }} />
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
      {evidence && (
        <div>
          <SectionLabel>Evidence</SectionLabel>
          <p className="mt-1 text-[12.5px] leading-relaxed text-ink-2">{evidence}</p>
        </div>
      )}
    </div>
  );
}

function CampaignInspector({ campaign }: { campaign: CampaignRow }) {
  return (
    <div className="grid gap-4 p-4">
      <div>
        <SectionLabel>Campaign</SectionLabel>
        <div className="mt-1 flex flex-wrap items-center gap-2">
          <span className="font-mono text-[15px] font-medium">{campaign.id}</span>
          <StateBadge state={campaign.state} />
        </div>
      </div>
      <dl className="divide-y divide-line">
        <Fact label="Accounts">{formatNumber(campaign.size)}</Fact>
        <Fact label="Campaign score">{formatNumber(campaign.score)}</Fact>
        <Fact label="Mean account score">{formatScore(campaign.mean_member_score)}</Fact>
        <Fact label="Mean link weight">{formatNumber(campaign.mean_edge_weight)}</Fact>
      </dl>
      {(campaign.top_edge_types?.length ?? 0) > 0 && (
        <div>
          <SectionLabel>Main link types</SectionLabel>
          <p className="mt-1 text-[12.5px] text-ink-2">{campaign.top_edge_types?.map(linkTypeLabel).join(", ")}</p>
        </div>
      )}
      <p className="text-[12px] text-ink-3">Select an account or a link in the graph to see its details.</p>
    </div>
  );
}

// ---------- below: the detail of the chosen campaign ----------

function CampaignDetails({ campaign, names }: { campaign: CampaignDetail; names: Map<string, string> }) {
  const members = campaign.members ?? [];
  const links = useMemo(() => [...(campaign.edges ?? [])].sort((a, b) => b.weight - a.weight), [campaign.edges]);
  const evidence = campaign.evidence ?? [];
  const cellHead = "h-8 text-[11.5px] font-medium text-ink-3 first:pl-4 last:pr-4";

  return (
    <>
      <Panel>
        <PanelHeader title="Prompt template" detail="recovered from the first prompts of the accounts" />
        {campaign.template ? (
          <pre className="overflow-x-auto p-4 font-mono text-[12px] leading-relaxed whitespace-pre-wrap text-ink-2">
            {campaign.template}
          </pre>
        ) : (
          <Quiet>No template yet. The watchdog looks for one after the campaign forms.</Quiet>
        )}
      </Panel>

      <div className="grid gap-4 xl:grid-cols-2">
        <Panel>
          <PanelHeader title="Accounts" detail={`${members.length}`} />
          <div className="max-h-96 overflow-y-auto">
            <Table>
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <TableHead className={cellHead}>Account</TableHead>
                  <TableHead className={cellHead}>State</TableHead>
                  <TableHead className={cellHead}>Score</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {members.map((member) => (
                  <TableRow
                    key={member.id}
                    onClick={() => navigate({ page: "account", id: member.id })}
                    className="cursor-pointer hover:bg-hover"
                  >
                    <TableCell className="py-1.5 pl-4">
                      <a
                        href={routeHref({ page: "account", id: member.id })}
                        onClick={(event) => event.stopPropagation()}
                        className="font-mono text-[12.5px] underline-offset-2 hover:underline"
                      >
                        {member.pseudonym}
                      </a>
                    </TableCell>
                    <TableCell className="py-1.5">
                      <StateBadge state={member.state} />
                    </TableCell>
                    <TableCell className="py-1.5 pr-4">
                      <ScoreCell score={member.score} />
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        </Panel>

        <Panel>
          <PanelHeader title="Campaign evidence" detail={`${evidence.length}`} />
          <div className="max-h-96 overflow-y-auto px-2 py-2">
            {evidence.length ? (
              <EvidenceList evidence={evidence} />
            ) : (
              <Quiet className="px-2 py-2">No campaign evidence yet.</Quiet>
            )}
          </div>
        </Panel>
      </div>

      <Panel>
        <PanelHeader
          title="Links"
          detail={links.length > LINKS_SHOWN ? `strongest ${LINKS_SHOWN} of ${links.length}` : `${links.length}`}
        />
        {links.length === 0 ? (
          <Quiet>This campaign has no links.</Quiet>
        ) : (
          <div className="max-h-[28rem] overflow-y-auto">
            <Table>
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <TableHead className={cellHead}>Account</TableHead>
                  <TableHead className={cellHead}>Account</TableHead>
                  <TableHead className={cn(cellHead, "text-right")}>Weight</TableHead>
                  <TableHead className={cellHead}>Link types</TableHead>
                  <TableHead className={cellHead}>Evidence</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {links.slice(0, LINKS_SHOWN).map((edge) => (
                  <TableRow key={`${edge.source}-${edge.target}`} className="hover:bg-hover">
                    <TableCell className="py-1.5 pl-4 font-mono text-[12px]">{names.get(edge.source) ?? "account"}</TableCell>
                    <TableCell className="py-1.5 font-mono text-[12px]">{names.get(edge.target) ?? "account"}</TableCell>
                    <TableCell className="py-1.5 text-right font-mono text-[12px] tabular-nums">
                      {formatNumber(edge.weight)}
                    </TableCell>
                    <TableCell className="py-1.5 text-[12px] text-ink-2">
                      {Object.entries(edge.types ?? {})
                        .map(([type, value]) => `${linkTypeLabel(type)} ${formatNumber(value)}`)
                        .join(", ")}
                    </TableCell>
                    <TableCell className="py-1.5 pr-4 text-[12px] whitespace-normal text-ink-2">{edge.evidence}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </Panel>

      <CaseFilePanel kind="campaign" id={campaign.id} />
    </>
  );
}

// ---------- the page ----------

export function Campaigns({ selected }: { selected: string | null }) {
  const [helpOpen, toggleHelp] = useHelp("campaigns");
  const [selection, setSelection] = useState<GraphSelection>(null);
  const campaigns = useCampaigns();
  const graphQuery = useGraph();
  const graph = graphQuery.data ?? EMPTY_GRAPH;

  // The detail of a link (its evidence sentence) is in the campaign of its accounts.
  const selectedNode = selection?.kind === "node" ? graph.nodes.find((node) => node.id === selection.id) : undefined;
  const selectedEdge =
    selection?.kind === "edge"
      ? graph.edges.find((edge) => sameLink(edge, selection.source, selection.target))
      : undefined;
  const edgeCampaignId = selectedEdge
    ? (graph.nodes.find((node) => node.id === selectedEdge.source)?.campaign_id ?? null)
    : null;

  const detail = useCampaign(selected);
  const edgeCampaign = useCampaign(edgeCampaignId);

  const names = useMemo(() => {
    const map = new Map<string, string>();
    for (const node of graph.nodes) map.set(node.id, node.pseudonym);
    for (const member of detail.data?.members ?? []) map.set(member.id, member.pseudonym);
    return map;
  }, [graph.nodes, detail.data?.members]);

  const rows = campaigns.data ?? [];
  const selectedRow = rows.find((row) => row.id === selected) ?? detail.data;
  const edgeEvidence =
    selectedEdge && edgeCampaign.data?.edges?.find((edge) => sameLink(edge, selectedEdge.source, selectedEdge.target))?.evidence;
  const unknownCampaign = selected && detail.isError && !detail.data;

  return (
    <>
      <PageHeader
        trail={
          selected ? (
            <a href={routeHref({ page: "campaigns" })} className="underline-offset-2 hover:text-foreground hover:underline">
              Campaigns
            </a>
          ) : undefined
        }
        title={selected ? <span className="font-mono font-medium">{selected}</span> : "Campaigns"}
        actions={
          <>
            {selected && !unknownCampaign && <CaseFileButton kind="campaign" id={selected} />}
            <HelpButton open={helpOpen} onToggle={toggleHelp} />
          </>
        }
      />
      <PageBody>
        <HelpNote open={helpOpen} onClose={toggleHelp}>
          A campaign is a group of accounts that work together. Each dot is one candidate account: the color shows the
          state and a larger dot has a higher score. A thick line is a strong link between two accounts. Select a
          campaign in the list, or an account or a link in the graph, to see the details.
        </HelpNote>

        {unknownCampaign && (
          <Problem>The watchdog does not know campaign {selected} now. It can be gone after a reset.</Problem>
        )}

        <div className="grid gap-4 lg:h-[min(66dvh,640px)] lg:grid-cols-[15rem_minmax(0,1fr)_19rem]">
          <Panel className="flex min-h-0 flex-col">
            <PanelHeader title="Campaigns" detail={`${rows.length}`} />
            <div className="max-h-64 min-h-0 flex-1 overflow-y-auto lg:max-h-none">
              {campaigns.isError && !rows.length ? (
                <div className="p-3">
                  <Problem>The campaign list did not load. {campaigns.error.message}</Problem>
                </div>
              ) : rows.length ? (
                <CampaignList campaigns={rows} selected={selected} />
              ) : (
                <Quiet>
                  {campaigns.isPending
                    ? "Loading the campaigns."
                    : "No campaigns yet. One forms when several suspicious accounts share tasks, timing or prompts."}
                </Quiet>
              )}
            </div>
          </Panel>

          <Panel className="relative flex min-h-0 flex-col overflow-hidden">
            <PanelHeader
              title="Account graph"
              detail={`${graph.nodes.length} accounts, ${graph.edges.length} links`}
              actions={
                selected && (
                  <Button asChild variant="ghost" size="xs" className="text-ink-2">
                    <a href={routeHref({ page: "campaigns" })}>
                      <X />
                      Show all accounts
                    </a>
                  </Button>
                )
              }
            />
            <div className="grid-canvas relative h-80 min-h-0 flex-1 lg:h-auto">
              {graph.nodes.length ? (
                <CampaignGraph graph={graph} campaignId={selected} selection={selection} onSelect={setSelection} />
              ) : (
                <Quiet>
                  {graphQuery.isPending
                    ? "Loading the graph."
                    : "No candidate accounts yet. Accounts appear here when the watchdog suspects them."}
                </Quiet>
              )}
              <ul
                aria-label="Meaning of the dot colors"
                className="pointer-events-none absolute bottom-2 left-3 flex flex-wrap gap-x-3 gap-y-1 text-[11.5px] text-ink-3"
              >
                {STATES.map((state) => (
                  <li key={state} className="flex items-center gap-1.5">
                    <span className={cn("size-2 rounded-full", STATE_META[state].dot)} aria-hidden="true" />
                    {STATE_META[state].label}
                  </li>
                ))}
              </ul>
            </div>
          </Panel>

          <Panel className="min-h-0 overflow-y-auto">
            <PanelHeader title="Details" />
            {selectedNode ? (
              <NodeInspector node={selectedNode} graph={graph} names={names} onSelect={setSelection} />
            ) : selectedEdge ? (
              <EdgeInspector
                edge={selectedEdge}
                evidence={edgeEvidence || undefined}
                names={names}
                onSelect={setSelection}
              />
            ) : selectedRow ? (
              <CampaignInspector campaign={selectedRow} />
            ) : (
              <Quiet>Select a campaign in the list, or an account or a link in the graph.</Quiet>
            )}
          </Panel>
        </div>

        {selected && detail.data && <CampaignDetails campaign={detail.data} names={names} />}
        {selected && detail.isPending && <Quiet>Loading the campaign.</Quiet>}
      </PageBody>
    </>
  );
}
