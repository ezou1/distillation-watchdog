import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import type { RatePoint } from "@/api";
import { formatNumber, formatSimTime } from "@/lib/format";

const SERIES = [
  { key: "served", label: "Served", color: "var(--viz-served)" },
  { key: "blocked", label: "Blocked", color: "var(--viz-blocked)" },
] as const;

export function RateLegend() {
  return (
    <ul className="flex items-center gap-3 text-[12px] text-ink-2">
      {SERIES.map((series) => (
        <li key={series.key} className="flex items-center gap-1.5">
          <span className="size-2 rounded-[2px]" style={{ background: series.color }} aria-hidden="true" />
          {series.label}
        </li>
      ))}
    </ul>
  );
}

interface TooltipBody {
  active?: boolean;
  label?: unknown;
  payload?: ReadonlyArray<{ dataKey?: unknown; value?: unknown }>;
}

function RateTooltip({ active, label, payload }: TooltipBody) {
  if (!active || !payload?.length) return null;
  const values = new Map(payload.map((entry) => [String(entry.dataKey), entry.value]));
  return (
    <div className="rounded-md border border-line-strong bg-raised px-3 py-2 text-[12px] shadow-pop">
      <div className="mb-1 font-mono text-[11px] text-ink-3">{formatSimTime(label)} sim</div>
      {SERIES.map((series) => (
        <div key={series.key} className="flex items-center gap-2">
          <span className="size-2 rounded-[2px]" style={{ background: series.color }} aria-hidden="true" />
          <span className="text-ink-2">{series.label}</span>
          <span className="ml-auto pl-4 font-mono tabular-nums">{formatNumber(values.get(series.key))}</span>
        </div>
      ))}
    </div>
  );
}

/**
 * Requests in each interval, served and blocked, each drawn from zero. One y-axis.
 * Not stacked: stacked, the blocked line traces served + blocked, so when nothing is blocked the pink line
 * sits on top of the served line and reads as "blocked".
 */
export function RateChart({ history }: { history: RatePoint[] }) {
  return (
    <div role="img" aria-label="Requests in each interval: served and blocked" className="h-full w-full">
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={history} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
          <CartesianGrid vertical={false} stroke="var(--viz-grid)" />
          <XAxis
            dataKey="t"
            tickFormatter={formatSimTime}
            minTickGap={56}
            tickLine={false}
            axisLine={{ stroke: "var(--viz-axis)" }}
            tick={{ fill: "var(--ink-3)", fontSize: 11 }}
          />
          <YAxis
            width={44}
            allowDecimals={false}
            tickLine={false}
            axisLine={false}
            tick={{ fill: "var(--ink-3)", fontSize: 11 }}
            tickFormatter={(value: number) => formatNumber(value)}
          />
          <Tooltip
            content={<RateTooltip />}
            cursor={{ stroke: "var(--viz-axis)", strokeWidth: 1 }}
            isAnimationActive={false}
          />
          {SERIES.map((series) => (
            <Area
              key={series.key}
              type="monotone"
              dataKey={series.key}
              stroke={series.color}
              strokeWidth={2}
              fill={series.color}
              fillOpacity={0.18}
              isAnimationActive={false}
              activeDot={{ r: 4, stroke: "var(--panel)", strokeWidth: 2 }}
            />
          ))}
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
