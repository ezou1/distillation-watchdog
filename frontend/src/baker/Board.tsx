import { useLayoutEffect, useRef, useState, type ReactNode } from "react";

import { Info, Pin, Stamp, plural } from "@/baker/parts";
import { RateSheet } from "@/baker/RateSheet";
import type { FeedItem, StreamState } from "@/hooks/useStream";
import { formatCount, formatNumber, formatSimDateTime } from "@/lib/format";
import { agentSite, generatorSite } from "@/lib/origin";
import { routeHref, type Route } from "@/lib/router";

// ---------- the layout tables ----------

export type CardId = "activity" | "accounts" | "messages" | "metrics" | "graph";

interface CardSpec {
  /** The number that the string table uses. */
  no: number;
  id: CardId;
  title: string;
  route: Route;
  /** The side of the postcard that has the two pins: the side that faces the other postcards. */
  pins: "left" | "right" | "top";
  /** The text of the "i" note: what the panel shows, and what a click does. */
  about: string[];
}

export const CARDS: readonly CardSpec[] = [
  { no: 1, id: "activity", pins: "right", title: "Recent activity", route: { page: "activity" },
    about: ["The newest decisions of the watchdog: an account is flagged, an account is blocked, or a new campaign is found.", "Select the postcard to see the full list."] },
  { no: 2, id: "accounts", pins: "left", title: "Accounts list", route: { page: "accounts" },
    about: ["The number of accounts in each state: blocked, suspicious, and OK.", "Select the postcard to see each account with its score. Select an account to open its dossier."] },
  { no: 3, id: "messages", pins: "top", title: "Suspicious messages", route: { page: "messages" },
    about: ["Messages that the watchdog flagged, from blocked and suspicious accounts.", "Select the postcard to read them. A message that many accounts sent is first."] },
  { no: 4, id: "metrics", pins: "left", title: "Active metrics", route: { page: "metrics" },
    about: ["Live numbers: requests each second, requests served, and requests blocked.", "Select the postcard to see all numbers of the watchdog."] },
  { no: 5, id: "graph", pins: "right", title: "Agent graph", route: { page: "campaigns" },
    about: ["A campaign is a group of accounts that work together. The number shows how many campaigns the watchdog found.", "Select the postcard to see the accounts and the links between them."] },
];

/**
 * The red strings between the postcards. To change a connection, change this table only.
 * `slack` is how much the string hangs: "tight" is almost straight, "droopy" has a clear sag.
 * Each postcard has two pins (0 is the upper or left pin, 1 is the lower or right pin). A string uses the
 * two pins that are nearest to each other, unless `pins` names them.
 */
export const STRINGS: ReadonlyArray<{ from: number; to: number; slack: "tight" | "droopy"; pins?: [0 | 1, 0 | 1] }> = [
  { from: 1, to: 2, slack: "tight" },
  { from: 1, to: 3, slack: "droopy" },
  { from: 2, to: 3, slack: "tight" },
  { from: 2, to: 4, slack: "droopy" },
  // The upper pin of each postcard, so this string goes along the top of the board and not behind the chart.
  { from: 1, to: 4, slack: "droopy", pins: [0, 0] },
  { from: 1, to: 5, slack: "tight" },
  { from: 3, to: 5, slack: "droopy" },
];

const SAG = { tight: 0.012, droopy: 0.06 } as const;

// ---------- strings ----------

interface Point {
  x: number;
  y: number;
}

/**
 * The line of one thread from pin to pin. It hangs by `sag` (a share of its length), and each point is moved
 * a little to the side, so the thread is not a perfect curve. The wobble is drawn, not filtered: an SVG
 * filter here makes the zoom animation slow.
 */
function threadPath(a: Point, b: Point, sag: number, seed: number): string {
  const length = Math.hypot(b.x - a.x, b.y - a.y) || 1;
  // A quadratic curve hangs half as far as its control point, so the control point goes down by two sags.
  const control = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 + 2 * sag * length };
  const steps = Math.max(6, Math.round(length / 16));
  const normal = { x: -(b.y - a.y) / length, y: (b.x - a.x) / length };
  let d = "";
  for (let step = 0; step <= steps; step++) {
    const t = step / steps;
    const u = 1 - t;
    // A fixed pseudo-random number for each point: the same string always gets the same shape.
    const noise = Math.sin(seed * 12.9898 + step * 78.233) * 43758.5453;
    const wobble = step === 0 || step === steps ? 0 : ((noise - Math.floor(noise)) - 0.5) * 2.2;
    const x = u * u * a.x + 2 * u * t * control.x + t * t * b.x + normal.x * wobble;
    const y = u * u * a.y + 2 * u * t * control.y + t * t * b.y + normal.y * wobble;
    d += `${step === 0 ? "M" : "L"}${x.toFixed(1)} ${y.toFixed(1)}`;
  }
  return d;
}

/**
 * Draws the strings from pin to pin. The pin positions come from the page, so the strings follow the layout.
 * A postcard has two pins. A string uses the two pins, one of each postcard, that are nearest to each other.
 */
function Strings() {
  const svg = useRef<SVGSVGElement | null>(null);
  const [cards, setCards] = useState<Map<number, Point[]>>(new Map());
  const [size, setSize] = useState({ width: 0, height: 0 });

  useLayoutEffect(() => {
    // The strings are measured against the cork, which is the parent of this drawing.
    const element = svg.current?.parentElement;
    if (!element) return;
    const measure = () => {
      const frame = element.getBoundingClientRect();
      const middle = (box: DOMRect): Point => ({ x: box.left + box.width / 2 - frame.left, y: box.top + box.height / 2 - frame.top });
      const next = new Map<number, Point[]>();
      for (const pin of element.querySelectorAll<SVGElement>("[data-bk-pin]")) {
        const no = Number(pin.dataset.bkPin);
        next.set(no, [...(next.get(no) ?? []), middle(pin.getBoundingClientRect())]);
      }
      setCards(next);
      setSize({ width: frame.width, height: frame.height });
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    // The fonts change the card sizes a little when they arrive.
    void document.fonts?.ready.then(measure);
    return () => observer.disconnect();
  }, []);

  /** The two pins, one of each postcard, that give the shortest string. */
  const shortest = (from: Point[], to: Point[]): [Point, Point] | undefined => {
    let best: [Point, Point] | undefined;
    let length = Infinity;
    for (const a of from) {
      for (const b of to) {
        const distance = Math.hypot(b.x - a.x, b.y - a.y);
        if (distance < length) [best, length] = [[a, b], distance];
      }
    }
    return best;
  };

  const paths = STRINGS.flatMap((string) => {
    const from = cards.get(string.from);
    const to = cards.get(string.to);
    if (!from || !to) return [];
    const named = string.pins && from[string.pins[0]] && to[string.pins[1]] ? ([from[string.pins[0]], to[string.pins[1]]] as const) : undefined;
    const ends = named ?? shortest(from, to);
    if (!ends) return [];
    const [a, b] = ends;
    if (!a || !b) return [];
    return [{ key: `${string.from}-${string.to}`, d: threadPath(a, b, SAG[string.slack], string.from * 31 + string.to) }];
  });

  return (
    <svg ref={svg} className="bk-strings" width={size.width} height={size.height} aria-hidden="true">
      {/* The shadow of the thread on the cork. */}
      <g transform="translate(2 5)" opacity="0.2">
        {paths.map((path) => (
          <path key={path.key} d={path.d} fill="none" stroke="var(--bk-ink)" strokeWidth="3.4" strokeLinecap="round" />
        ))}
      </g>
      <g strokeLinecap="round" strokeLinejoin="round">
        {paths.map((path) => (
          <g key={path.key}>
            <path d={path.d} fill="none" stroke="var(--bk-red)" strokeWidth="2.6" />
            {/* The twist of the thread: short lighter marks along it. */}
            <path d={path.d} fill="none" stroke="var(--bk-red-light)" strokeWidth="1" strokeDasharray="2.5 4.5" opacity="0.8" />
          </g>
        ))}
      </g>
    </svg>
  );
}

// ---------- postcards ----------

function Postcard({ spec, children }: { spec: CardSpec; children: ReactNode }) {
  return (
    <div className={`bk-card bk-card--${spec.id} bk-pins-${spec.pins}`} data-bk-card={spec.id}>
      <Pin id={String(spec.no)} className="bk-card__pin bk-card__pin--a" />
      <Pin id={String(spec.no)} className="bk-card__pin bk-card__pin--b" />
      <span className="bk-card__frank" aria-hidden="true">
        No. {spec.no}
      </span>
      <a className="bk-card__link" href={routeHref(spec.route)}>
        <h2>{spec.title}</h2>
        <div className="bk-card__body">{children}</div>
      </a>
      <Info title={spec.title}>
        {spec.about.map((text) => (
          <p key={text}>{text}</p>
        ))}
      </Info>
    </div>
  );
}

function feedLine(item: FeedItem): string {
  if (item.kind === "campaign") return `${item.event.id}: ${plural(item.event.size, "account", "accounts")} linked`;
  return `${item.event.pseudonym} ${item.kind === "block" ? "blocked" : "suspicious"}`;
}

function MiniGraph() {
  // A fixed small picture of linked accounts. It is decoration; the numbers beside it are live.
  const nodes: Array<[number, number, boolean]> = [
    [14, 30, true], [40, 12, true], [62, 36, true], [34, 52, true], [86, 16, false], [92, 50, false],
  ];
  const links: Array<[number, number, boolean]> = [[0, 1, true], [1, 2, true], [0, 3, true], [2, 3, true], [1, 3, true], [2, 4, false], [4, 5, false]];
  return (
    <svg className="bk-minigraph" viewBox="0 0 104 64" aria-hidden="true">
      {links.map(([a, b, red]) => (
        <line
          key={`${a}-${b}`}
          x1={nodes[a]![0]} y1={nodes[a]![1]} x2={nodes[b]![0]} y2={nodes[b]![1]}
          stroke={red ? "var(--bk-red)" : "var(--bk-gray)"} strokeWidth={red ? 1.4 : 0.8} opacity={red ? 0.9 : 0.6}
        />
      ))}
      {nodes.map(([x, y, red]) => (
        <circle key={`${x}-${y}`} cx={x} cy={y} r={red ? 4.5 : 3} fill={red ? "var(--bk-red)" : "var(--bk-gray)"} stroke="var(--bk-ink)" strokeWidth="0.8" />
      ))}
    </svg>
  );
}

function CardBody({ id, stream }: { id: CardId; stream: StreamState }) {
  const summary = stream.summary;
  const counts = summary?.accounts ?? {};
  const blocked = counts.block ?? 0;
  const suspicious = counts.review ?? 0;
  const ok = (counts.normal ?? 0) + (counts.disclosed ?? 0) + (counts.insufficient ?? 0);

  switch (id) {
    case "activity":
      return stream.feed.length ? (
        <ul className="bk-lines">
          {stream.feed.slice(0, 3).map((item) => (
            <li key={item.key} className={item.kind === "block" ? "bk-red-text" : undefined}>
              {feedLine(item)}
            </li>
          ))}
        </ul>
      ) : blocked + suspicious > 0 ? (
        <p>
          No new flags since you opened this page. Before that: <b>{formatNumber(blocked)}</b> blocked,{" "}
          <b>{formatNumber(suspicious)}</b> suspicious.
        </p>
      ) : (
        <p>No flags yet.</p>
      );
    case "accounts":
      return (
        <ul className="bk-tally">
          <li><Stamp state="block" /> <b>{formatNumber(blocked)}</b></li>
          <li><Stamp state="review" /> <b>{formatNumber(suspicious)}</b></li>
          <li><Stamp state="normal" /> <b>{formatNumber(ok)}</b></li>
        </ul>
      );
    case "messages":
      return blocked + suspicious > 0 ? (
        <p>
          Flagged messages from <b>{plural(blocked + suspicious, "account", "accounts")}</b>. The most common are first.
        </p>
      ) : (
        <p>No flagged messages yet.</p>
      );
    case "metrics":
      return (
        <dl className="bk-figures">
          <div><dt>Requests each second</dt><dd>{summary ? formatNumber(Math.round(summary.rps * 10) / 10) : "–"}</dd></div>
          <div><dt>Served</dt><dd>{summary ? formatCount(summary.served) : "–"}</dd></div>
          <div><dt>Blocked</dt><dd className="bk-red-text">{summary ? formatCount(summary.blocked) : "–"}</dd></div>
        </dl>
      );
    case "graph":
      return (
        <div className="bk-card__graph">
          <MiniGraph />
          <p><b>{plural(summary?.campaigns ?? 0, "campaign", "campaigns")}</b> found</p>
        </div>
      );
  }
}

// ---------- props ----------

const SITE_TAGS: ReadonlyArray<{ label: string; href: () => string }> = [
  { label: "Normal traffic", href: () => generatorSite("normal") },
  { label: "Attack traffic", href: () => generatorSite("attack") },
  { label: "Agent chat", href: agentSite },
];

function statusWord(online: boolean | null): string {
  return online === null ? "Connecting" : online ? "Live" : "Offline";
}

// ---------- the board ----------

export function Board({ stream, away, onSearch }: { stream: StreamState; away: boolean; onSearch: () => void }) {
  const simTime = formatSimDateTime(stream.summary?.sim_time).slice(0, 16);

  return (
    // `inert` keeps the keyboard out of the board while a display covers it.
    <main className="bk-board" data-away={away || undefined} inert={away} aria-label="Evidence board">
      <div className="bk-board__cork">
        <h1 className="bk-plate">221B Baker Street</h1>

        {CARDS.map((spec) => (
          <Postcard key={spec.id} spec={spec}>
            <CardBody id={spec.id} stream={stream} />
          </Postcard>
        ))}

        <Strings />

        <div className="bk-center">
          <RateSheet summary={stream.summary} online={stream.online} />
        </div>

        <a className="bk-folder-prop" href={routeHref({ page: "eval" })} data-bk-card="eval">
          <span className="bk-folder-prop__tab" aria-hidden="true" />
          <span className="bk-folder-prop__label">Evaluation</span>
          <span className="bk-folder-prop__note">Case report</span>
        </a>

        <nav className="bk-tags" aria-label="Other sites">
          {SITE_TAGS.map((tag) => (
            <a key={tag.label} className="bk-tag" href={tag.href()} title="Opens the other site">
              <span className="bk-tag__hole" aria-hidden="true" />
              {tag.label}
            </a>
          ))}
        </nav>

        <p className="bk-status bk-perf-left" role="status" data-online={String(stream.online)}>
          <b>{statusWord(stream.online)}</b>
          <span>{simTime ? `${simTime} simulated time` : "No simulated time yet"}</span>
        </p>

        <button type="button" className="bk-search" onClick={onSearch} title="Search accounts and campaigns">
          Search <kbd>Ctrl K</kbd>
        </button>
      </div>
    </main>
  );
}
