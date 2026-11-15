// E. Agent graph: the account graph, the campaign list, and the detail of one campaign.
//
// The accounts of the campaigns are highlighted. All other accounts and links stay on the graph, faded.
// HIGHLIGHT sets which campaign accounts count.

import { useMemo, useRef, useState } from "react";

import type { CampaignDetail, CampaignRow, Graph, GraphEdge, GraphNode } from "@/api";
import { DisplayFrame, Note, Stamp, Swatch, emptyText, linkWord, plural, toneOf, type Tone } from "@/baker/parts";
import { CaseFileButton, CaseFilePanel } from "@/components/CaseFile";
import { EvidenceList } from "@/components/EvidenceList";
import { useCampaign, useCampaigns, useGraph } from "@/hooks/queries";
import { useApp } from "@/lib/app-context";
import { formatNumber, formatScore } from "@/lib/format";
import { routeHref } from "@/lib/router";
import { stateMeta } from "@/lib/states";

type Selection = { kind: "node"; id: string } | { kind: "edge"; source: string; target: string } | null;

/**
 * Which accounts the graph highlights.
 *   "campaign":  each account that is in a campaign.
 *   "same-task": only a campaign account that has one or more "same task text" links.
 * The demo traffic makes campaigns from "same timing" links and no "same task text" links, so "same-task"
 * highlights nothing there. For that reason the setting is "campaign".
 */
const HIGHLIGHT: "campaign" | "same-task" = "campaign";

const EMPTY_GRAPH: Graph = { nodes: [], edges: [] };
const SPACING = 30;
const GOLDEN_ANGLE = 2.399963;
const LOOSE = "";
/** Width divided by height of the picture area, for the choice of the layout. */
const PICTURE_ASPECT = 1.3;
/** Above this number of red links, a click on a link is off: the links are too close to select one. */
const CLICKABLE_LINKS = 600;
const LABELS_UP_TO = 36;
const TONE_FILL: Record<Tone, string> = { red: "var(--bk-red)", yellow: "var(--bk-yellow)", gray: "var(--bk-gray)" };

function sameLink(edge: { source: string; target: string }, a: string, b: string): boolean {
  return (edge.source === a && edge.target === b) || (edge.source === b && edge.target === a);
}

function hasSharedTask(edge: GraphEdge): boolean {
  return (edge.types?.shared_task ?? 0) > 0;
}

interface Placed {
  positions: Map<string, { x: number; y: number }>;
  clusters: Array<{ id: string; x: number; y: number; radius: number }>;
  width: number;
  height: number;
}

/**
 * Put each campaign in its own round group, and the accounts with no campaign in one more group.
 * The place of an account depends only on its campaign and its rank in the sorted group, so the
 * picture does not move when the data refreshes with the same accounts.
 */
function place(nodes: GraphNode[]): Placed {
  const groups = new Map<string, GraphNode[]>();
  for (const node of nodes) {
    const key = node.campaign_id ?? LOOSE;
    groups.set(key, [...(groups.get(key) ?? []), node]);
  }
  const order = [...groups.keys()].sort((a, b) => (a === LOOSE ? 1 : b === LOOSE ? -1 : a.localeCompare(b)));
  const sized = order.map((id) => {
    const members = [...(groups.get(id) ?? [])].sort((a, b) => a.id.localeCompare(b.id));
    return { id, members, radius: SPACING * Math.sqrt(members.length) + 34 };
  });
  // Try each possible row width and keep the one that fills a wide picture best.
  const pack = (rowWidth: number) => {
    const spots: Array<{ x: number; y: number }> = [];
    let x = 0;
    let y = 0;
    let rowHeight = 0;
    let width = 0;
    for (const group of sized) {
      const size = 2 * group.radius;
      if (x > 0 && x + size > rowWidth + 0.5) {
        x = 0;
        y += rowHeight;
        rowHeight = 0;
      }
      spots.push({ x: x + group.radius, y: y + group.radius });
      x += size;
      width = Math.max(width, x);
      rowHeight = Math.max(rowHeight, size);
    }
    return { spots, width: Math.max(width, 1), height: Math.max(y + rowHeight, 1) };
  };
  let best = pack(Infinity);
  let bestScale = Math.min(PICTURE_ASPECT / best.width, 1 / best.height);
  let sum = 0;
  for (const group of sized) {
    sum += 2 * group.radius;
    const candidate = pack(sum);
    const scale = Math.min(PICTURE_ASPECT / candidate.width, 1 / candidate.height);
    if (scale > bestScale) [best, bestScale] = [candidate, scale];
  }

  const positions = new Map<string, { x: number; y: number }>();
  const clusters: Placed["clusters"] = [];
  sized.forEach((group, slot) => {
    const { x: cx, y: cy } = best.spots[slot]!;
    clusters.push({ id: group.id, x: cx, y: cy, radius: group.radius });
    group.members.forEach((node, index) => {
      const distance = SPACING * Math.sqrt(index + 0.5);
      const angle = index * GOLDEN_ANGLE;
      positions.set(node.id, { x: cx + distance * Math.cos(angle), y: cy + distance * Math.sin(angle) });
    });
  });
  return { positions, clusters, width: best.width, height: best.height };

}

function GraphPicture({
  graph,
  campaignId,
  selection,
  onSelect,
}: {
  graph: Graph;
  campaignId: string | null;
  selection: Selection;
  onSelect: (selection: Selection) => void;
}) {
  const view = useMemo(() => {
    const placed = place(graph.nodes);
    const byId = new Map(graph.nodes.map((node) => [node.id, node]));
    const withTask = new Set<string>();
    for (const edge of graph.edges) {
      if (!hasSharedTask(edge)) continue;
      withTask.add(edge.source);
      withTask.add(edge.target);
    }
    const lit = new Set(
      graph.nodes.filter((node) => node.campaign_id && (HIGHLIGHT === "campaign" || withTask.has(node.id))).map((node) => node.id),
    );
    const linked = (edge: GraphEdge) =>
      HIGHLIGHT === "campaign" ? byId.get(edge.source)?.campaign_id === byId.get(edge.target)?.campaign_id : hasSharedTask(edge);
    const red: GraphEdge[] = [];
    let faded = "";
    for (const edge of graph.edges) {
      const a = placed.positions.get(edge.source);
      const b = placed.positions.get(edge.target);
      if (!a || !b) continue;
      if (lit.has(edge.source) && lit.has(edge.target) && linked(edge)) red.push(edge);
      else faded += `M${a.x.toFixed(1)} ${a.y.toFixed(1)}L${b.x.toFixed(1)} ${b.y.toFixed(1)}`;
    }
    return { placed, byId, lit, red, faded };
  }, [graph]);

  const { placed, byId, lit, red, faded } = view;
  // The account under the pointer (or with the keyboard focus).
  const [hot, setHotId] = useState<string | null>(null);
  // Where the label goes: the place of the account inside the picture frame, in pixels.
  const [tipAt, setTipAt] = useState<{ x: number; y: number } | null>(null);
  const frame = useRef<HTMLDivElement | null>(null);
  const setHot = (id: string | null, element?: Element) => {
    setHotId(id);
    const box = element?.getBoundingClientRect();
    const outer = frame.current?.getBoundingClientRect();
    setTipAt(box && outer ? { x: box.left + box.width / 2 - outer.left, y: box.top - outer.top } : null);
  };
  const hotNode = hot ? byId.get(hot) : undefined;
  const hotAt = hot ? placed.positions.get(hot) : undefined;
  const hotLinks = hot ? graph.edges.filter((edge) => edge.source === hot || edge.target === hot) : [];
  const inCampaign = (id: string) => !campaignId || byId.get(id)?.campaign_id === campaignId;
  // Many links on one spot make a solid red area, so each link is lighter when there are many.
  const redOpacity = Math.max(0.1, Math.min(0.85, 9 / Math.sqrt(red.length || 1)));
  const labels = lit.size <= LABELS_UP_TO;
  const picked = (id: string) =>
    selection?.kind === "node" ? selection.id === id : selection?.kind === "edge" ? selection.source === id || selection.target === id : false;
  const pad = 26;

  return (
    <div className="bk-graphwrap" ref={frame}>
    <svg
      className="bk-graph"
      viewBox={`${-pad} ${-pad} ${placed.width + 2 * pad} ${placed.height + 2 * pad}`}
      preserveAspectRatio="xMidYMid meet"
      role="img"
      aria-label={`Account graph: ${graph.nodes.length} accounts, ${lit.size} highlighted`}
      onClick={() => onSelect(null)}
    >
      {placed.clusters.map((cluster) => (
        <g key={cluster.id || "loose"} opacity={!campaignId || cluster.id === campaignId ? 1 : 0.35}>
          <circle cx={cluster.x} cy={cluster.y} r={cluster.radius - 8} className="bk-graph__ring" />
          <text x={cluster.x} y={cluster.y - cluster.radius + 8} textAnchor="middle" className="bk-graph__group">
            {cluster.id || "No campaign"}
          </text>
        </g>
      ))}
      <path d={faded} className="bk-graph__faded" />
      <g opacity={redOpacity} className="bk-graph__threads">
        {red.map((edge) => {
          const a = placed.positions.get(edge.source)!;
          const b = placed.positions.get(edge.target)!;
          const dim = !(inCampaign(edge.source) && inCampaign(edge.target));
          return (
            <line
              key={`${edge.source}|${edge.target}`}
              x1={a.x} y1={a.y} x2={b.x} y2={b.y}
              strokeWidth={Math.min(4, 0.8 + (edge.weight ?? 0) * 0.6)}
              opacity={dim ? 0.2 : 1}
            />
          );
        })}
      </g>
      {red.length <= CLICKABLE_LINKS &&
        red.map((edge) => {
          const a = placed.positions.get(edge.source)!;
          const b = placed.positions.get(edge.target)!;
          const isPicked = selection?.kind === "edge" && sameLink(edge, selection.source, selection.target);
          return (
            <line
              key={`${edge.source}|${edge.target}`}
              x1={a.x} y1={a.y} x2={b.x} y2={b.y}
              className="bk-graph__hit"
              data-picked={isPicked || undefined}
              onClick={(event) => {
                event.stopPropagation();
                onSelect({ kind: "edge", source: edge.source, target: edge.target });
              }}
            />
          );
        })}
      {graph.nodes.map((node) => {
        const at = placed.positions.get(node.id);
        if (!at) return null;
        const isLit = lit.has(node.id);
        const dim = !inCampaign(node.id);
        return (
          <g
            key={node.id}
            transform={`translate(${at.x.toFixed(1)} ${at.y.toFixed(1)})`}
            className="bk-graph__node"
            data-lit={isLit || undefined}
            data-picked={picked(node.id) || undefined}
            data-hot={hot === node.id || undefined}
            onMouseEnter={(event) => setHot(node.id, event.currentTarget)}
            onMouseLeave={() => setHot(null)}
            onFocus={(event) => setHot(node.id, event.currentTarget)}
            onBlur={() => setHot(null)}
            opacity={hot === node.id ? 1 : dim ? 0.25 : isLit ? 1 : 0.5}
            tabIndex={isLit ? 0 : -1}
            role="button"
            aria-label={`${node.pseudonym}, ${isLit ? "highlighted" : "not highlighted"}`}
            onClick={(event) => {
              event.stopPropagation();
              onSelect({ kind: "node", id: node.id });
            }}
            onKeyDown={(event) => {
              if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                onSelect({ kind: "node", id: node.id });
              }
            }}
          >
            {/* A wide clear circle, so the pointer finds a small dot easily. */}
            <circle r="13" fill="transparent" />
            <g className="bk-graph__pin">
              {isLit ? (
                <>
                  <circle r="9.5" fill={TONE_FILL[toneOf(node.state)]} stroke="var(--bk-ink)" strokeWidth="1.4" />
                  <circle cx="-2.6" cy="-3" r="2.4" fill="var(--bk-paper)" opacity="0.5" />
                </>
              ) : (
                <circle r="5" fill={TONE_FILL[toneOf(node.state)]} stroke="var(--bk-ink-3)" strokeWidth="0.8" />
              )}
            </g>
          </g>
        );
      })}
      {/* The pointer is over an account: its links show in dark red, and a small label gives its name and state. */}
      {hotAt &&
        hotLinks.map((edge) => {
          const other = placed.positions.get(edge.source === hot ? edge.target : edge.source);
          return other ? (
            <line key={`${edge.source}|${edge.target}`} x1={hotAt.x} y1={hotAt.y} x2={other.x} y2={other.y} className="bk-graph__hot" />
          ) : null;
        })}
      {/* The names are drawn last, so no pin covers them. */}
      {graph.nodes.map((node) => {
        const at = placed.positions.get(node.id);
        if (!at || !((labels && lit.has(node.id)) || picked(node.id))) return null;
        return (
          <text key={node.id} x={at.x} y={at.y + 23} textAnchor="middle" className="bk-graph__label" data-picked={picked(node.id) || undefined}>
            {node.pseudonym}
            {node.you ? " (you)" : ""}
          </text>
        );
      })}
    </svg>
      {hotNode && tipAt && (
        <p className="bk-graph-tip" style={{ left: tipAt.x, top: tipAt.y }} role="status">
          <b>{hotNode.pseudonym}</b>
          {stateMeta(hotNode.state).label}, score {formatScore(hotNode.score)}, {plural(hotLinks.length, "link", "links")}
          <span>Select it to see the details.</span>
        </p>
      )}
    </div>
  );
}

// ---------- the detail card ----------

function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd>{children}</dd>
    </div>
  );
}

function NodeCard({ node, graph, names, onSelect }: { node: GraphNode; graph: Graph; names: Map<string, string>; onSelect: (s: Selection) => void }) {
  const links = graph.edges.filter((edge) => edge.source === node.id || edge.target === node.id).sort((a, b) => b.weight - a.weight);
  return (
    <>
      <h3>Account</h3>
      <p className="bk-typed bk-big">{node.pseudonym}</p>
      <dl className="bk-facts">
        <Fact label="State"><Stamp state={node.state} /></Fact>
        <Fact label="Score">{formatScore(node.score)}</Fact>
        <Fact label="Campaign">
          {node.campaign_id ? <a href={routeHref({ page: "campaigns", id: node.campaign_id })}>{node.campaign_id}</a> : "None"}
        </Fact>
      </dl>
      <a className="bk-button" href={routeHref({ page: "account", id: node.id })}>
        Open the dossier
      </a>
      <h3>Strongest links ({links.length})</h3>
      {links.length === 0 ? (
        <p>This account has no link to another account.</p>
      ) : (
        <ul className="bk-linklist">
          {links.slice(0, 8).map((edge) => {
            const other = edge.source === node.id ? edge.target : edge.source;
            return (
              <li key={`${edge.source}|${edge.target}`}>
                <button type="button" onClick={() => onSelect({ kind: "edge", source: edge.source, target: edge.target })}>
                  <span className="bk-typed">{names.get(other) ?? "account"}</span>
                  <span>{Object.keys(edge.types ?? {}).map(linkWord).join(", ")}</span>
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </>
  );
}

function EdgeCard({ edge, evidence, names, onSelect }: { edge: GraphEdge; evidence: string | undefined; names: Map<string, string>; onSelect: (s: Selection) => void }) {
  const types = Object.entries(edge.types ?? {}).sort((a, b) => b[1] - a[1]);
  return (
    <>
      <h3>Link between two accounts</h3>
      {[edge.source, edge.target].map((id) => (
        <p key={id}>
          <button type="button" className="bk-typed bk-textbutton" onClick={() => onSelect({ kind: "node", id })}>
            {names.get(id) ?? "account"}
          </button>
        </p>
      ))}
      <h3>Why they are linked</h3>
      {types.length === 0 ? (
        <p>The watchdog gave no reason.</p>
      ) : (
        <dl className="bk-facts">
          {types.map(([type, value]) => (
            <Fact key={type} label={linkWord(type)}>
              {formatNumber(value)}
            </Fact>
          ))}
          <Fact label="Link strength">{formatNumber(edge.weight)}</Fact>
        </dl>
      )}
      {evidence && (
        <>
          <h3>Evidence</h3>
          <p>{evidence}</p>
        </>
      )}
    </>
  );
}

function CampaignCard({ campaign }: { campaign: CampaignRow }) {
  return (
    <>
      <h3>Campaign</h3>
      <p className="bk-typed bk-big">{campaign.id}</p>
      <dl className="bk-facts">
        <Fact label="State"><Stamp state={campaign.state} /></Fact>
        <Fact label="Accounts">{formatNumber(campaign.size)}</Fact>
        <Fact label="Campaign score">{formatNumber(campaign.score)}</Fact>
        <Fact label="Mean account score">{formatScore(campaign.mean_member_score)}</Fact>
        {(campaign.top_edge_types?.length ?? 0) > 0 && (
          <Fact label="Main links">{campaign.top_edge_types?.map(linkWord).join(", ")}</Fact>
        )}
      </dl>
      <p>Select an account or a red link on the graph to see its details.</p>
    </>
  );
}

function CampaignPapers({ campaign }: { campaign: CampaignDetail }) {
  const members = campaign.members ?? [];
  const evidence = campaign.evidence ?? [];
  return (
    <div className="bk-papers">
      <section className="bk-paper">
        <h2 className="bk-heading">Accounts of {campaign.id} ({members.length})</h2>
        <div className="bk-papers__scroll">
          <table className="bk-table">
            <thead>
              <tr>
                <th>Account</th>
                <th>State</th>
                <th className="bk-num">Score</th>
              </tr>
            </thead>
            <tbody>
              {members.map((member) => (
                <tr key={member.id}>
                  <td>
                    <a href={routeHref({ page: "account", id: member.id })}>{member.pseudonym}</a>
                  </td>
                  <td>
                    <Stamp state={member.state} />
                  </td>
                  <td className="bk-num">{formatScore(member.score)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
      <section className="bk-paper">
        <h2 className="bk-heading">Campaign evidence ({evidence.length})</h2>
        <div className="bk-papers__scroll bk-reuse">
          {evidence.length ? <EvidenceList evidence={evidence} /> : <Note>No campaign evidence yet.</Note>}
        </div>
      </section>
      {campaign.template && (
        <section className="bk-paper bk-papers__wide">
          <h2 className="bk-heading">Prompt template</h2>
          <pre className="bk-typed bk-pre">{campaign.template}</pre>
        </section>
      )}
      <div className="bk-papers__wide bk-reuse">
        <CaseFilePanel kind="campaign" id={campaign.id} />
      </div>
    </div>
  );
}

// ---------- the display ----------

export function AgentGraphDisplay({ selected }: { selected: string | null }) {
  const { stream } = useApp();
  const [selection, setSelection] = useState<Selection>(null);
  const campaigns = useCampaigns();
  const graphQuery = useGraph();
  const graph = graphQuery.data ?? EMPTY_GRAPH;
  const rows = campaigns.data ?? [];

  const selectedNode = selection?.kind === "node" ? graph.nodes.find((node) => node.id === selection.id) : undefined;
  const selectedEdge = selection?.kind === "edge" ? graph.edges.find((edge) => sameLink(edge, selection.source, selection.target)) : undefined;
  // The sentence that explains a link is in the campaign of its accounts.
  const edgeCampaignId = selectedEdge ? (graph.nodes.find((node) => node.id === selectedEdge.source)?.campaign_id ?? null) : null;
  const detail = useCampaign(selected);
  const edgeCampaign = useCampaign(edgeCampaignId);
  const edgeEvidence =
    selectedEdge && edgeCampaign.data?.edges?.find((edge) => sameLink(edge, selectedEdge.source, selectedEdge.target))?.evidence;

  const names = useMemo(() => {
    const map = new Map<string, string>();
    for (const node of graph.nodes) map.set(node.id, node.pseudonym);
    return map;
  }, [graph.nodes]);
  const selectedRow = rows.find((row) => row.id === selected) ?? detail.data;
  const unknown = selected && detail.isError && !detail.data;

  return (
    <DisplayFrame
      title="Agent graph"
      note="Accounts and the links between them. A large pin is an account in a campaign. Red thread links the accounts of one campaign."
      back={{ page: "live" }}
      actions={selected && !unknown ? <span className="bk-reuse"><CaseFileButton kind="campaign" id={selected} /></span> : undefined}
    >
      {unknown && <Note>The watchdog does not know campaign {selected} now. It can be gone after a reset.</Note>}
      <div className="bk-graphdesk">
        <section className="bk-paper bk-campaigns" aria-label="Campaigns">
          <h2 className="bk-heading">Campaigns ({rows.length})</h2>
          {rows.length ? (
            <ul>
              {selected && (
                <li>
                  <a className="bk-campaign bk-campaign--all" href={routeHref({ page: "campaigns" })}>
                    Show all accounts
                  </a>
                </li>
              )}
              {rows.map((campaign) => (
                <li key={campaign.id}>
                  <a
                    className="bk-campaign"
                    href={routeHref({ page: "campaigns", id: campaign.id })}
                    aria-current={campaign.id === selected ? "true" : undefined}
                    onClick={() => setSelection(null)}
                  >
                    <span className="bk-typed">{campaign.id}</span>
                    <Stamp state={campaign.state} />
                    <span>
                      {plural(campaign.size, "account", "accounts")}, score {formatNumber(campaign.score)}
                    </span>
                    {(campaign.top_edge_types?.length ?? 0) > 0 && <span>{campaign.top_edge_types?.map(linkWord).join(", ")}</span>}
                  </a>
                </li>
              ))}
            </ul>
          ) : (
            <Note>
              {campaigns.isPending
                ? "Loading the campaigns."
                : emptyText(stream.online, "No campaigns yet. One forms when suspicious accounts share tasks or timing.")}
            </Note>
          )}
        </section>

        <section className="bk-photo" aria-label="Account graph">
          {graph.nodes.length ? (
            <GraphPicture graph={graph} campaignId={selected} selection={selection} onSelect={setSelection} />
          ) : (
            <Note>
              {graphQuery.isPending
                ? "Loading the graph."
                : emptyText(stream.online, "No suspected accounts yet. Accounts show here when the watchdog suspects them.")}
            </Note>
          )}
          <ul className="bk-legend" aria-label="Meaning of the marks">
            <li><Swatch tone="red" /> Blocked</li>
            <li><Swatch tone="yellow" /> Suspicious</li>
            <li><Swatch tone="gray" /> OK</li>
            <li><span className="bk-legend__thread" aria-hidden="true" /> Link in a campaign</li>
            <li>Large pin: in a campaign. Small dot: no campaign. Point at an account to see its name and links.</li>
          </ul>
        </section>

        <section className="bk-paper bk-details" aria-label="Details" aria-live="polite">
          {selectedNode ? (
            <NodeCard node={selectedNode} graph={graph} names={names} onSelect={setSelection} />
          ) : selectedEdge ? (
            <EdgeCard edge={selectedEdge} evidence={edgeEvidence || undefined} names={names} onSelect={setSelection} />
          ) : selectedRow ? (
            <CampaignCard campaign={selectedRow} />
          ) : (
            <>
              <h3>Details</h3>
              <p>Select a campaign in the list, or an account on the graph.</p>
            </>
          )}
        </section>
      </div>
      {selected && detail.data && <CampaignPapers campaign={detail.data} />}
      {selected && detail.isPending && <Note>Loading the campaign.</Note>}
    </DisplayFrame>
  );
}
