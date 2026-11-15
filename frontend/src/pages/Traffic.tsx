import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Play, Square } from "lucide-react";
import { useEffect, useState } from "react";

import { generator, type GeneratorStatus, type PersonaCounters } from "@/api";
import { HelpButton, HelpNote, Problem, Quiet, useHelp } from "@/components/Notes";
import { PageBody, PageHeader } from "@/components/PageHeader";
import { Panel, PanelHeader } from "@/components/Panel";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { formatNumber, formatPercent, formatSimDateTime } from "@/lib/format";
import { hasDashboard, watchdogSite, type GeneratorKind, type Role } from "@/lib/origin";
import { routeHref } from "@/lib/router";
import { cn } from "@/lib/utils";

const POLL_MS = 1000;
const SCALES = ["small", "demo", "full"] as const;

export const TRAFFIC_TITLE: Record<GeneratorKind, string> = { normal: "Normal traffic", attack: "Attack traffic" };

/** What each persona does, in plain words. Only this operator page shows it; the watchdog never sees personas. */
const PERSONAS: Record<string, { name: string; does: string }> = {
  solo_dev: { name: "Solo developers", does: "Real developer sessions, replayed from SWE-chat. One person per account." },
  saas_app: { name: "SaaS app", does: "A product that calls the model for its own users, with many user IDs on one account." },
  benchmark_runner: { name: "Benchmark runner", does: "An evaluation job that declares its use up front." },
  hydra: { name: "Hydra", does: "Many accounts that send the same conversations again and again." },
  fake_env: { name: "Fake environment", does: "Accounts that feed the model invented tool outputs instead of a real machine." },
  stealth: { name: "Stealth", does: "Accounts that copy normal developer use and spread it over 48 simulated hours." },
};

function persona(name: string) {
  return PERSONAS[name] ?? { name, does: "" };
}

/** Leaked sessions as a share of the sessions that can leak. Null when the generator has not reported the total. */
function leakShare(leaked: number, planned: number | null | undefined): number | null {
  return typeof planned === "number" && planned > 0 ? leaked / planned : null;
}

/** One decimal place, but a share above zero never reads as "0.0%". */
function formatLeakShare(share: number): string {
  return share > 0 && share < 0.0005 ? "<0.1%" : formatPercent(share);
}

function totals(personas: Record<string, PersonaCounters>) {
  const all = Object.values(personas);
  // The leak rate of the page covers the personas that have sent requests. A persona that never started has
  // leaked nothing, and its planned sessions would only make the rate look better than it is.
  const started = all.filter((c) => c.sent > 0);
  const plannedKnown = started.length > 0 && started.every((c) => leakShare(c.leaked, c.sessions_planned) !== null);
  return {
    sent: all.reduce((sum, c) => sum + c.sent, 0),
    served: all.reduce((sum, c) => sum + c.served, 0),
    blocked: all.reduce((sum, c) => sum + c.blocked, 0),
    leaked: all.reduce((sum, c) => sum + c.leaked, 0),
    planned: plannedKnown ? started.reduce((sum, c) => sum + (c.sessions_planned ?? 0), 0) : null,
  };
}

/** Radar's two-tone headline, from the generator's own counters. */
function Overview({ kind, status }: { kind: GeneratorKind; status: GeneratorStatus | undefined }) {
  const personas = status?.personas ?? {};
  const running = Object.entries(personas).filter(([, c]) => c.running).map(([name]) => persona(name).name);
  const sum = totals(personas);

  const lead = !status
    ? "Connecting to the generator."
    : running.length === 0
      ? kind === "attack"
        ? "No attack is running."
        : "No normal traffic is running."
      : `${new Intl.ListFormat("en", { type: "conjunction" }).format(running)} ${running.length === 1 ? "is" : "are"} running.`;

  let rest: string;
  if (!status) rest = "The page tries again every second.";
  else if (sum.sent === 0) rest = "Start a persona below. Its accounts register with the lab, then its requests begin.";
  else {
    rest = `The gateway served ${formatNumber(sum.served)} of ${formatNumber(sum.sent)} requests and refused ${formatNumber(sum.blocked)}.`;
    if (kind === "attack" && sum.leaked > 0) {
      const share = leakShare(sum.leaked, sum.planned);
      rest +=
        share === null
          ? ` Before the blocks, ${formatNumber(sum.leaked)} complete ${sum.leaked === 1 ? "session" : "sessions"} got through.`
          : ` Before the blocks, ${formatLeakShare(share)} of the complete sessions got through (${formatNumber(sum.leaked)} of ${formatNumber(sum.planned)}).`;
    }
  }

  return (
    <section className="radar-band -mx-4 -mt-4 border-b border-line px-4 pt-10 pb-7 sm:-mx-6 sm:-mt-6 sm:px-6 sm:pt-14">
      <h1 className="display max-w-[42ch] text-[24px] sm:text-[30px]" aria-live="polite">
        <span className="text-foreground">{lead}</span> <span className="text-brand-ink/75">{rest}</span>
      </h1>
      {status?.sim_time ? (
        <p className="mt-5 font-mono text-[12px] text-ink-3 tabular-nums">
          {formatSimDateTime(status.sim_time)} sim · scale {status.scale} · speed {formatNumber(status.speed)}×
        </p>
      ) : null}
    </section>
  );
}

function Count({ label, value, className }: { label: string; value: number; className?: string }) {
  return (
    <div className="min-w-0">
      <div className="text-[11.5px] text-ink-3">{label}</div>
      <div className={cn("font-mono text-[14px] tabular-nums", className)}>{formatNumber(value)}</div>
    </div>
  );
}

/** One persona as a Linear row: who it is and what it does, its counters, and the one action it has. */
function PersonaRow({
  name,
  counters,
  busy,
  onToggle,
}: {
  name: string;
  counters: PersonaCounters;
  busy: boolean;
  onToggle: () => void;
}) {
  const meta = persona(name);
  const share = counters.sent > 0 ? counters.blocked / counters.sent : 0;
  const leak = leakShare(counters.leaked, counters.sessions_planned);
  return (
    <li className="grid gap-x-6 gap-y-4 px-5 py-4 lg:grid-cols-[minmax(0,1.4fr)_minmax(0,2fr)_auto] lg:items-center">
      <div className="min-w-0">
        <div className="flex items-center gap-2.5">
          <span className="relative flex size-2 shrink-0">
            {counters.running && (
              <span className="absolute inline-flex size-full animate-ping rounded-full bg-st-normal opacity-50" />
            )}
            <span className={cn("relative inline-flex size-2 rounded-full", counters.running ? "bg-st-normal" : "bg-ink-4")} />
          </span>
          <h3 className="text-[14px] font-semibold tracking-[-0.01em]">{meta.name}</h3>
          <span className={cn("text-[12px]", counters.running ? "text-st-normal-ink" : "text-ink-4")}>
            {counters.running ? "Running" : "Stopped"}
          </span>
        </div>
        {meta.does && <p className="mt-1 text-[12.5px] leading-relaxed text-ink-3">{meta.does}</p>}
      </div>

      <div className="grid min-w-0 grid-cols-3 gap-4 sm:grid-cols-5">
        <Count label="Accounts" value={counters.accounts} />
        <Count label="Sent" value={counters.sent} />
        <Count label="Served" value={counters.served} />
        <div className="min-w-0">
          <div className="text-[11.5px] text-ink-3">Blocked</div>
          <div className={cn("font-mono text-[14px] tabular-nums", counters.blocked > 0 && "text-st-block-ink")}>
            {formatNumber(counters.blocked)}
          </div>
          <div
            className="mt-1.5 h-1 overflow-hidden rounded-full bg-hover"
            role="meter"
            aria-label="Share of requests blocked"
            aria-valuemin={0}
            aria-valuemax={1}
            aria-valuenow={share}
          >
            <div className="risk-fill h-full rounded-full" style={{ width: `${share * 100}%` }} />
          </div>
        </div>
        {leak === null ? (
          <Count label="Leaked" value={counters.leaked} />
        ) : (
          <div className="min-w-0">
            <div className="text-[11.5px] text-ink-3">Leaked</div>
            <div className="font-mono text-[14px] tabular-nums">{formatLeakShare(leak)}</div>
            <div className="mt-0.5 font-mono text-[11.5px] text-ink-3 tabular-nums">
              {formatNumber(counters.leaked)} of {formatNumber(counters.sessions_planned)}
            </div>
          </div>
        )}
      </div>

      <Button
        size="sm"
        variant={counters.running ? "outline" : "default"}
        disabled={busy}
        onClick={onToggle}
        className={cn("w-24 justify-self-start lg:justify-self-end", counters.running && "border-line-strong bg-transparent")}
      >
        {counters.running ? <Square /> : <Play />}
        {busy ? (counters.running ? "Stopping" : "Starting") : counters.running ? "Stop" : "Start"}
      </Button>
    </li>
  );
}

export function Traffic({ kind, role }: { kind: GeneratorKind; role: Role }) {
  const [helpOpen, toggleHelp] = useHelp(`traffic-${kind}`);
  const client = useQueryClient();
  const api = generator(kind);
  const query = useQuery({ queryKey: ["generator", kind], queryFn: api.status, refetchInterval: POLL_MS });
  const status = query.data;

  // Scale and speed apply when a persona starts. They begin at the generator's own values.
  const [scale, setScale] = useState<string>("demo");
  const [speed, setSpeed] = useState<number>(600);
  const [touched, setTouched] = useState(false);
  useEffect(() => {
    if (status && !touched) {
      setScale(status.scale);
      setSpeed(status.speed);
    }
  }, [status, touched]);

  const [busy, setBusy] = useState<string | null>(null);
  const [failure, setFailure] = useState<string | null>(null);

  const toggle = async (name: string, running: boolean) => {
    setBusy(name);
    setFailure(null);
    try {
      if (running) await api.stop(name);
      else await api.start(name, scale, speed);
    } catch (error) {
      setFailure(`${persona(name).name} did not ${running ? "stop" : "start"}. ${(error as Error).message}`);
    } finally {
      setBusy(null);
      await client.invalidateQueries({ queryKey: ["generator", kind] });
    }
  };

  // Account states live on the dashboard: a link inside the app, or to the watchdog server.
  const dashboardHref = hasDashboard(role) ? routeHref({ page: "live" }) : watchdogSite() + routeHref({ page: "live" });

  return (
    <>
      <PageHeader
        title={TRAFFIC_TITLE[kind]}
        actions={
          <>
            <label className="flex items-center gap-2 text-[12.5px] text-ink-3">
              Scale
              <Select
                value={scale}
                onValueChange={(value) => {
                  setTouched(true);
                  setScale(value);
                }}
              >
                <SelectTrigger size="sm" className="h-7 w-24 border-line bg-transparent px-2 text-[12px] text-foreground" aria-label="Scale">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent align="end">
                  {SCALES.map((value) => (
                    <SelectItem key={value} value={value} className="text-[12.5px]">
                      {value}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </label>
            <label className="flex items-center gap-2 text-[12.5px] text-ink-3">
              Speed
              <input
                type="number"
                min={1}
                step={60}
                value={speed}
                onChange={(event) => {
                  setTouched(true);
                  setSpeed(Number(event.target.value) || 1);
                }}
                className="h-7 w-20 rounded-md border border-line bg-transparent px-2 font-mono text-[12px] text-foreground tabular-nums"
              />
            </label>
            <HelpButton open={helpOpen} onToggle={toggleHelp} />
          </>
        }
      />
      <PageBody>
        <Overview kind={kind} status={status} />

        <HelpNote open={helpOpen} onClose={toggleHelp}>
          Each row is one persona: a kind of {kind === "attack" ? "distillation attack" : "normal customer"}. Start
          sends its requests to the lab at the scale and speed above; speed is simulated seconds per real second.
          Blocked counts refused requests, not accounts. Leaked is the share of a persona's complete sessions that
          it received before its accounts were blocked, with the count below it. For the state of each account, see the{" "}
          <a href={dashboardHref} className="text-brand-ink underline-offset-2 hover:underline">
            dashboard
          </a>
          .
        </HelpNote>

        {(failure || status?.error) && <Problem>{failure ?? status?.error}</Problem>}

        <Panel>
          <PanelHeader
            title="Personas"
            detail={status ? `${Object.keys(status.personas).length}` : undefined}
          />
          {query.isError && !status ? (
            <div className="p-4">
              <Problem>
                The {kind} generator does not answer. Start the demo with <code className="font-mono">scripts/demo.sh</code>.
              </Problem>
            </div>
          ) : !status ? (
            <Quiet>Loading the personas.</Quiet>
          ) : (
            <ul className="divide-y divide-line">
              {Object.entries(status.personas).map(([name, counters]) => (
                <PersonaRow
                  key={name}
                  name={name}
                  counters={counters}
                  busy={busy === name}
                  onToggle={() => toggle(name, counters.running)}
                />
              ))}
            </ul>
          )}
        </Panel>
      </PageBody>
    </>
  );
}
