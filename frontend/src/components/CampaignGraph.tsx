import type { CollectionArgument, Core, ElementDefinition, EventObject, StylesheetJsonBlock } from "cytoscape";
import { useEffect, useMemo, useRef, useState } from "react";
import CytoscapeComponent from "react-cytoscapejs";

import type { Graph } from "@/api";
import { stateMeta } from "@/lib/states";
import { useCssColors } from "@/lib/theme";

export type GraphSelection = { kind: "node"; id: string } | { kind: "edge"; source: string; target: string } | null;

// Cytoscape cannot read CSS variables, so the colors are resolved here. All of these are plain hex values.
const COLOR_VARS = [
  "--st-block",
  "--st-review",
  "--st-normal",
  "--st-disclosed",
  "--st-insufficient",
  "--foreground",
  "--ink-3",
  "--ink-4",
  "--panel",
  "--brand",
] as const;

const NODE_MIN = 14;
const NODE_RANGE = 22;

const MAX_FIT_ZOOM = 1.15;

function edgeId(source: string, target: string): string {
  return `${source}__${target}`;
}

/** Bring elements into view, but do not enlarge a small group past its natural size. */
function fitTo(cy: Core, elements?: CollectionArgument): void {
  cy.fit(elements, 48);
  if (cy.zoom() > MAX_FIT_ZOOM) {
    cy.zoom(MAX_FIT_ZOOM);
    cy.center(elements);
  }
}

/**
 * The account graph. Node color is the state, node size is the score, line width is the link weight.
 * The accounts of the chosen campaign stay bright; the others fade.
 */
export function CampaignGraph({
  graph,
  campaignId,
  selection,
  onSelect,
}: {
  graph: Graph;
  campaignId: string | null;
  selection: GraphSelection;
  onSelect: (selection: GraphSelection) => void;
}) {
  const colors = useCssColors(COLOR_VARS);
  const [cy, setCy] = useState<Core | null>(null);
  const cyRef = useRef<Core | null>(null);
  const onSelectRef = useRef(onSelect);
  onSelectRef.current = onSelect;

  const elements = useMemo<ElementDefinition[]>(() => {
    const ids = new Set(graph.nodes.map((node) => node.id));
    const nodes: ElementDefinition[] = graph.nodes.map((node) => ({
      group: "nodes",
      data: {
        id: node.id,
        label: node.pseudonym + (node.you ? " (you)" : ""),
        color: colors[stateMeta(node.state).fillVar as (typeof COLOR_VARS)[number]] || colors["--st-insufficient"],
        size: NODE_MIN + NODE_RANGE * Math.max(0, Math.min(1, node.score ?? 0)),
        campaign: node.campaign_id ?? "",
        you: node.you ? 1 : 0,
      },
    }));
    const edges: ElementDefinition[] = graph.edges
      .filter((edge) => ids.has(edge.source) && ids.has(edge.target))
      .map((edge) => ({
        group: "edges",
        data: { id: edgeId(edge.source, edge.target), source: edge.source, target: edge.target, weight: edge.weight ?? 0 },
      }));
    return [...nodes, ...edges];
  }, [graph, colors]);

  const stylesheet = useMemo<StylesheetJsonBlock[]>(
    () => [
      {
        selector: "node",
        style: {
          "background-color": "data(color)",
          width: "data(size)",
          height: "data(size)",
          label: "data(label)",
          color: colors["--ink-3"],
          "font-size": 12,
          "font-family": "JetBrains Mono Variable, ui-monospace, monospace",
          "min-zoomed-font-size": 9,
          "text-valign": "bottom",
          "text-margin-y": 5,
          "text-background-color": colors["--panel"],
          "text-background-opacity": 0.85,
          "text-background-padding": "2px",
          "text-background-shape": "roundrectangle",
          "border-width": 2,
          "border-color": colors["--panel"],
          "overlay-opacity": 0,
        },
      },
      { selector: "node[you = 1]", style: { shape: "diamond", "border-color": colors["--foreground"] } },
      {
        selector: "edge",
        style: {
          width: "mapData(weight, 0, 4, 0.75, 6)",
          "line-color": colors["--ink-4"],
          opacity: 0.45,
          "curve-style": "haystack",
          "overlay-opacity": 0,
        },
      },
      { selector: ".dim", style: { opacity: 0.12, "text-opacity": 0, "text-background-opacity": 0 } },
      { selector: "edge.dim", style: { opacity: 0.05 } },
      { selector: "edge.member", style: { opacity: 0.7 } },
      {
        selector: "node.picked",
        style: { "border-width": 3, "border-color": colors["--brand"], color: colors["--foreground"], "min-zoomed-font-size": 0 },
      },
      { selector: "edge.picked", style: { "line-color": colors["--brand"], opacity: 1 } },
    ],
    [colors],
  );

  // Run the layout only when the set of accounts and links changes, not on each poll.
  // A campaign is close to a clique (every account linked to every other), so the springs win against
  // weak repulsion and the nodes collapse into a touching ball with stacked labels. Strong repulsion and
  // soft springs keep about 100 px between nodes, enough for a label.
  const signature = useMemo(() => elements.map((element) => element.data.id).sort().join("|"), [elements]);
  // The layout is also bounded to the box's shape: unbounded, the campaigns stack into a tall column that a
  // wide box can only show at a tiny zoom.
  useEffect(() => {
    if (!cy || !signature) return;
    cy.resize();
    cy.layout({
      name: "cose",
      animate: false,
      randomize: true,
      nodeRepulsion: () => 250000,
      edgeElasticity: () => 100,
      idealEdgeLength: () => 90,
      nodeOverlap: 20,
      componentSpacing: 80,
      gravity: 0.25,
      padding: 36,
      boundingBox: { x1: 0, y1: 0, w: Math.max(cy.width(), 320), h: Math.max(cy.height(), 320) },
    }).run();
  }, [cy, signature]);

  // Bring the chosen campaign (or everything) into view. Kept in a ref so the resize observer uses the latest choice.
  const refitRef = useRef<() => void>(() => {});
  refitRef.current = () => {
    if (!cy) return;
    const members = campaignId ? cy.nodes().filter((node) => node.data("campaign") === campaignId) : null;
    fitTo(cy, members && !members.empty() ? members : undefined);
  };

  // Cytoscape measures its box once, when it starts. The panel settles to its final size later and changes with
  // the window, so without this the graph is fitted to a stale size and sits partly or fully out of view.
  useEffect(() => {
    if (!cy || typeof ResizeObserver === "undefined") return;
    const container = cy.container();
    if (!container) return;
    const observer = new ResizeObserver(() => {
      cy.resize();
      refitRef.current();
    });
    observer.observe(container);
    return () => observer.disconnect();
  }, [cy]);

  useEffect(() => {
    if (!cy) return;
    const onNode = (event: EventObject) => onSelectRef.current({ kind: "node", id: event.target.id() as string });
    const onEdge = (event: EventObject) =>
      onSelectRef.current({
        kind: "edge",
        source: event.target.data("source") as string,
        target: event.target.data("target") as string,
      });
    const onBackground = (event: EventObject) => {
      if (event.target === cy) onSelectRef.current(null);
    };
    cy.on("tap", "node", onNode);
    cy.on("tap", "edge", onEdge);
    cy.on("tap", onBackground);
    return () => {
      cy.off("tap", "node", onNode);
      cy.off("tap", "edge", onEdge);
      cy.off("tap", onBackground);
    };
  }, [cy]);

  // Fade the accounts outside the chosen campaign and bring the campaign into view.
  useEffect(() => {
    if (!cy) return;
    cy.elements().removeClass("dim member");
    if (campaignId) {
      const members = cy.nodes().filter((node) => node.data("campaign") === campaignId);
      if (!members.empty()) {
        const links = members.edgesWith(members);
        cy.elements().difference(members.union(links)).addClass("dim");
        links.addClass("member");
      }
    }
    cy.resize();
    refitRef.current();
  }, [cy, campaignId, signature]);

  useEffect(() => {
    if (!cy) return;
    cy.elements().removeClass("picked");
    if (selection?.kind === "node") cy.getElementById(selection.id).addClass("picked");
    if (selection?.kind === "edge") {
      cy.getElementById(edgeId(selection.source, selection.target)).addClass("picked");
      cy.getElementById(selection.source).addClass("picked");
      cy.getElementById(selection.target).addClass("picked");
    }
  }, [cy, selection, signature]);

  return (
    <CytoscapeComponent
      elements={elements}
      stylesheet={stylesheet}
      layout={{ name: "preset" }}
      minZoom={0.15}
      maxZoom={3}
      style={{ width: "100%", height: "100%" }}
      cy={(core: Core) => {
        if (cyRef.current !== core) {
          cyRef.current = core;
          setCy(core);
        }
      }}
    />
  );
}
