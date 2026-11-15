import { useEffect, useMemo, useRef, useState } from "react";
import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import type { Summary } from "@/api";
import { Info, Pin } from "@/baker/parts";
import { RateStore, WINDOWS, type RateWindow } from "@/baker/rateStore";
import { formatNumber, formatSimDateTime } from "@/lib/format";

const WINDOW_KEY = "baker-rate-window";
const TICKS = 6;

const SERIES = [
  { key: "served", label: "Served", color: "var(--bk-ink-2)" },
  { key: "blocked", label: "Blocked", color: "var(--bk-red)" },
] as const;

function savedWindow(): RateWindow {
  try {
    const id = localStorage.getItem(WINDOW_KEY);
    return WINDOWS.find((window) => window.id === id) ?? WINDOWS[0]!;
  } catch {
    return WINDOWS[0]!;
  }
}

/** Clock time for short windows, the date for long ones. Simulated time, UTC. */
function tickText(window: RateWindow, seconds: number): string {
  const text = formatSimDateTime(seconds);
  if (!text) return "";
  if (window.span <= 3600) return text.slice(11, 16);
  if (window.span <= 48 * 3600) return `${text.slice(8, 10)} ${text.slice(11, 16)}`;
  return text.slice(5, 10);
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
    <div className="bk-tip">
      <div>{formatSimDateTime(label).slice(0, 16)}</div>
      {SERIES.map((series) => (
        <div key={series.key} className="bk-tip__row">
          <span className="bk-swatch" style={{ background: series.color }} aria-hidden="true" />
          {series.label}
          <b>{formatNumber(Math.round(Number(values.get(series.key) ?? 0) * 10) / 10)}</b>
        </div>
      ))}
    </div>
  );
}

/**
 * The sheet in the middle of the board: requests served and blocked, in a fixed window of simulated time.
 * The time axis always shows the full window that ends now. Where the page has no data, the chart is empty.
 */
export function RateSheet({ summary, online }: { summary: Summary | null; online: boolean | null }) {
  const store = useRef<RateStore | null>(null);
  store.current ??= new RateStore();
  const [window_, setWindow] = useState(savedWindow);

  // Merge during render is not safe, so the merge runs in an effect and the bins are computed after it.
  const [version, setVersion] = useState(0);
  useEffect(() => {
    if (!summary) return;
    store.current?.add(summary);
    setVersion((value) => value + 1);
  }, [summary]);

  useEffect(() => {
    const save = () => store.current?.save(true);
    window.addEventListener("pagehide", save);
    return () => {
      window.removeEventListener("pagehide", save);
      save();
    };
  }, []);

  const end = store.current.now();
  const data = useMemo(
    () => (end > 0 ? store.current!.bins(window_, end) : []),
    // `version` changes when the store gets new data.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [window_, end, version],
  );
  const start = end - window_.span;
  const ticks = useMemo(
    () => Array.from({ length: TICKS + 1 }, (_, index) => start + (index * window_.span) / TICKS),
    [start, window_.span],
  );

  const choose = (next: RateWindow) => {
    setWindow(next);
    try {
      localStorage.setItem(WINDOW_KEY, next.id);
    } catch {
      // Storage can be blocked. The choice still applies for this visit.
    }
  };

  return (
    <section className="bk-sheet" aria-label="Request rate">
      <Pin className="bk-sheet__pin bk-sheet__pin--left" />
      <Pin className="bk-sheet__pin bk-sheet__pin--right" />
      <header className="bk-sheet__head">
        <div>
          <h2>Request rate</h2>
          <p>Requests in each simulated minute</p>
        </div>
        <Info title="Request rate">
          <p>How many requests the gateway served, and how many it blocked, in each simulated minute.</p>
          <p>Select 1h, 24h, 48h, 1w, or 1m to set the length of time that the chart shows. The time is simulated time, which runs faster than real time.</p>
          <p>This page collects the data while it is open. A long window fills as the demo runs.</p>
        </Info>
        <div className="bk-windows" role="group" aria-label="Time window, in simulated time">
          {WINDOWS.map((option) => (
            <button
              key={option.id}
              type="button"
              aria-pressed={option.id === window_.id}
              onClick={() => choose(option)}
              title={`Show the last ${option.label} of simulated time`}
            >
              {option.label}
            </button>
          ))}
        </div>
      </header>
      <div className="bk-sheet__plot">
        {end > 0 ? (
          <div role="img" aria-label="Requests in each simulated minute: served and blocked" className="bk-fill">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={data} margin={{ top: 6, right: 28, bottom: 0, left: 0 }}>
                <CartesianGrid vertical={false} stroke="var(--bk-rule)" />
                <XAxis
                  dataKey="t"
                  type="number"
                  domain={[start, end]}
                  ticks={ticks}
                  allowDataOverflow
                  tickFormatter={(value: number) => tickText(window_, value)}
                  tickLine={{ stroke: "var(--bk-ink-3)" }}
                  axisLine={{ stroke: "var(--bk-ink-3)" }}
                  tick={{ fill: "var(--bk-ink-2)", fontSize: 13, fontFamily: "var(--bk-type)" }}
                />
                <YAxis
                  width={46}
                  tickLine={false}
                  axisLine={false}
                  tick={{ fill: "var(--bk-ink-2)", fontSize: 13, fontFamily: "var(--bk-type)" }}
                  tickFormatter={(value: number) => formatNumber(value)}
                />
                <Tooltip content={<RateTooltip />} cursor={{ stroke: "var(--bk-ink-3)", strokeWidth: 1 }} isAnimationActive={false} />
                {SERIES.map((series) => (
                  <Area
                    key={series.key}
                    type="monotone"
                    dataKey={series.key}
                    stroke={series.color}
                    strokeWidth={1.8}
                    fill={series.color}
                    fillOpacity={series.key === "blocked" ? 0.22 : 0.1}
                    isAnimationActive={false}
                    connectNulls={false}
                    dot={false}
                    activeDot={{ r: 3.5, stroke: "var(--bk-paper)", strokeWidth: 1.5 }}
                  />
                ))}
              </AreaChart>
            </ResponsiveContainer>
          </div>
        ) : (
          <p className="bk-note">
            {online === false
              ? "The watchdog is offline. This page tries again by itself."
              : "No requests yet. Start a traffic generator."}
          </p>
        )}
      </div>
      <footer className="bk-sheet__foot">
        <ul>
          {SERIES.map((series) => (
            <li key={series.key}>
              <span className="bk-swatch" style={{ background: series.color }} aria-hidden="true" />
              {series.label}
            </li>
          ))}
        </ul>
        <span>Simulated time (UTC). The window ends at the current simulated time.</span>
      </footer>
    </section>
  );
}
