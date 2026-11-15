import type { ScorePoint } from "@/api";
import { stateMeta } from "@/lib/states";

const WIDTH = 96;
const HEIGHT = 24;
const PAD = 2;
const MAX_POINTS = 40;

/** Score history of one account, 0 (bottom) to 1 (top). The line takes the color of the current state. */
export function Sparkline({ history, state }: { history: ScorePoint[] | undefined; state: string }) {
  const points = (history ?? []).slice(-MAX_POINTS);
  if (points.length === 0) return <span className="text-ink-4">–</span>;

  const first = points[0]?.[0] ?? 0;
  const last = points[points.length - 1]?.[0] ?? first;
  const span = last - first || 1;
  const coords = points.map(([time, score]) => {
    const x = points.length === 1 ? WIDTH / 2 : PAD + ((time - first) / span) * (WIDTH - 2 * PAD);
    const y = HEIGHT - PAD - Math.max(0, Math.min(1, score)) * (HEIGHT - 2 * PAD);
    return [x, y] as const;
  });
  const end = coords[coords.length - 1];
  const color = `var(${stateMeta(state).fillVar})`;

  return (
    <svg width={WIDTH} height={HEIGHT} viewBox={`0 0 ${WIDTH} ${HEIGHT}`} role="img" aria-label="Score history">
      <line x1={0} x2={WIDTH} y1={HEIGHT - PAD} y2={HEIGHT - PAD} stroke="var(--viz-grid)" strokeWidth={1} />
      <polyline
        fill="none"
        stroke={color}
        strokeWidth={1.5}
        strokeLinejoin="round"
        strokeLinecap="round"
        points={coords.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(" ")}
      />
      {end && <circle cx={end[0]} cy={end[1]} r={2} fill={color} />}
    </svg>
  );
}
