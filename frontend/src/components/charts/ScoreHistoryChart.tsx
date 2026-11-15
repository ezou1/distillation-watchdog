import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import type { ScorePoint } from "@/api";
import { formatScore, formatSimTime } from "@/lib/format";

interface TooltipBody {
  active?: boolean;
  label?: unknown;
  payload?: ReadonlyArray<{ value?: unknown }>;
}

function ScoreTooltip({ active, label, payload }: TooltipBody) {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded-md border border-line-strong bg-raised px-3 py-2 text-[12px] shadow-pop">
      <div className="font-mono text-[11px] text-ink-3">{formatSimTime(label)} sim</div>
      <div>
        Score <span className="font-mono tabular-nums">{formatScore(payload[0]?.value)}</span>
      </div>
    </div>
  );
}

/** The score of one account over simulation time. The y-axis is fixed at 0 to 1. */
export function ScoreHistoryChart({ history }: { history: ScorePoint[] }) {
  const data = history.map(([t, score]) => ({ t, score }));
  return (
    <div role="img" aria-label="Score over time" className="h-full w-full">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
          <CartesianGrid vertical={false} stroke="var(--viz-grid)" />
          <XAxis
            dataKey="t"
            type="number"
            domain={["dataMin", "dataMax"]}
            tickFormatter={formatSimTime}
            minTickGap={56}
            tickLine={false}
            axisLine={{ stroke: "var(--viz-axis)" }}
            tick={{ fill: "var(--ink-3)", fontSize: 11 }}
          />
          <YAxis
            width={32}
            domain={[0, 1]}
            ticks={[0, 0.5, 1]}
            tickLine={false}
            axisLine={false}
            tick={{ fill: "var(--ink-3)", fontSize: 11 }}
          />
          <Tooltip
            content={<ScoreTooltip />}
            cursor={{ stroke: "var(--viz-axis)", strokeWidth: 1 }}
            isAnimationActive={false}
          />
          <Line
            type="monotone"
            dataKey="score"
            stroke="var(--viz-neutral)"
            strokeWidth={2}
            dot={data.length <= 2}
            isAnimationActive={false}
            activeDot={{ r: 4, stroke: "var(--panel)", strokeWidth: 2 }}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
