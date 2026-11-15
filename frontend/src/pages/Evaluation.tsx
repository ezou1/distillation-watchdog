import { Check, Info, RefreshCw, X } from "lucide-react";
import { useState, type ReactNode } from "react";

import { HelpButton, HelpNote, Problem, Quiet, useHelp } from "@/components/Notes";
import { PageBody, PageHeader } from "@/components/PageHeader";
import { Panel, PanelHeader } from "@/components/Panel";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useEvaluation } from "@/hooks/queries";
import { formatNumber, formatPercent, humanizeKey } from "@/lib/format";
import { cn } from "@/lib/utils";

// The report files belong to the offline evaluation (dw.eval). Their shape can change, so every value is read
// defensively: a part that is missing is not shown, and the last section shows every value as it is.

type Dict = Record<string, unknown>;

function dict(value: unknown): Dict | undefined {
  return value !== null && typeof value === "object" && !Array.isArray(value) ? (value as Dict) : undefined;
}

function at(value: unknown, ...path: string[]): unknown {
  let current = value;
  for (const key of path) current = dict(current)?.[key];
  return current;
}

function num(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

const PERSONA_LABEL: Record<string, string> = {
  hydra: "Hydra",
  fake_env: "Fake environment",
  stealth: "Stealth",
  solo_dev: "Solo developer",
  saas_app: "SaaS app",
  benchmark_runner: "Benchmark runner",
  none: "No campaign",
};
const ATTACK_PERSONAS = new Set(["hydra", "fake_env", "stealth"]);

const personaLabel = (key: string): string => PERSONA_LABEL[key] ?? humanizeKey(key);

// ---------- small building blocks ----------

interface Column {
  label: string;
  align?: "right";
  /** Let the text of this column wrap. */
  wrap?: boolean;
}

function DataTable({ columns, rows }: { columns: Column[]; rows: ReactNode[][] }) {
  return (
    <Table>
      <TableHeader>
        <TableRow className="hover:bg-transparent">
          {columns.map((column) => (
            <TableHead
              key={column.label}
              className={cn(
                "h-8 text-[11.5px] font-medium text-ink-3 first:pl-4 last:pr-4",
                column.align === "right" && "text-right",
              )}
            >
              {column.label}
            </TableHead>
          ))}
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((cells, rowIndex) => (
          <TableRow key={rowIndex} className="hover:bg-hover">
            {cells.map((cell, cellIndex) => {
              const column = columns[cellIndex];
              return (
                <TableCell
                  key={cellIndex}
                  className={cn(
                    "py-1.5 text-[12.5px] first:pl-4 last:pr-4",
                    column?.align === "right" && "text-right font-mono text-[12px] tabular-nums",
                    column?.wrap && "whitespace-normal",
                  )}
                >
                  {cell}
                </TableCell>
              );
            })}
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

/** A share from 0 to 1 as a short bar with the percent after it. */
function ShareBar({ share }: { share: number | undefined }) {
  if (share === undefined) return <span className="text-ink-4">–</span>;
  return (
    <span className="flex items-center gap-2">
      <span className="h-1.5 w-20 overflow-hidden rounded-full bg-hover">
        <span className="block h-full rounded-full bg-ink-2" style={{ width: `${Math.max(0, Math.min(1, share)) * 100}%` }} />
      </span>
      <span className="font-mono text-[12px] tabular-nums">{formatPercent(share, 0)}</span>
    </span>
  );
}

function Facts({ items }: { items: Array<[string, ReactNode]> }) {
  const shown = items.filter(([, value]) => value !== undefined && value !== null && value !== "–");
  return (
    <dl className="grid gap-x-8 gap-y-3 p-4 sm:grid-cols-2 xl:grid-cols-3">
      {shown.map(([label, value]) => (
        <div key={label}>
          <dt className="text-[12px] text-ink-3">{label}</dt>
          <dd className="mt-0.5 font-mono text-[14px] tabular-nums">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

function Note({ children }: { children: ReactNode }) {
  return (
    <div className="border-t border-line px-4 py-2.5">
      <p className="max-w-[80ch] text-[12px] leading-relaxed text-ink-3">{children}</p>
    </div>
  );
}

// ---------- sections ----------

function Comparison({ metrics }: { metrics: unknown }) {
  const pr = dict(at(metrics, "per_request_vs_per_account"));
  if (!pr) return null;
  const accountAuc = num(at(metrics, "account_roc", "auc"));
  const perDay = num(pr.requests_per_day);
  const rows: Array<{ label: string; request: ReactNode; requestNote?: string; account: ReactNode; accountNote?: string }> = [
    {
      label: "Normal use that is flagged by mistake",
      request: formatNumber(num(pr.false_request_flags_per_day)),
      requestNote: `false flags each day at ${formatNumber(perDay)} requests (${formatPercent(num(pr.request_fpr))} of normal requests)`,
      account: `${formatNumber(num(pr.falsely_blocked_normal_accounts))} of ${formatNumber(num(pr.normal_accounts))}`,
      accountNote: `normal accounts blocked by mistake (${formatPercent(num(pr.account_fpr))})`,
    },
    {
      label: "Attack that is caught",
      request: formatPercent(num(pr.request_recall)),
      requestNote: "of attack requests are flagged",
      account: formatPercent(num(pr.attack_account_recall)),
      accountNote: "of attack accounts are blocked",
    },
    {
      label: "How well the score separates attack from normal",
      request: formatNumber(num(pr.request_auc)),
      requestNote: "AUC of the request model",
      account: formatNumber(accountAuc),
      accountNote: "AUC of the account model",
    },
  ];
  return (
    <Panel>
      <PanelHeader title="One request against the whole account" detail="the same holdout traffic, judged two ways" />
      <div className="overflow-x-auto">
        <table className="w-full min-w-[640px] border-collapse text-left">
          <thead>
            <tr className="border-b border-line">
              <th className="w-[30%] px-4 py-2" />
              <th className="px-4 py-2 text-[12px] font-medium text-ink-3">Judging each request alone</th>
              <th className="px-4 py-2 text-[12px] font-medium text-ink-3">Judging the account</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.label} className="border-b border-line align-top last:border-b-0">
                <th className="px-4 py-3 text-[12.5px] font-normal text-ink-2">{row.label}</th>
                {[
                  [row.request, row.requestNote],
                  [row.account, row.accountNote],
                ].map(([value, note], index) => (
                  <td key={index} className="px-4 py-3">
                    <div className="text-[24px] leading-none font-semibold tracking-[-0.03em] tabular-nums">{value}</div>
                    <div className="mt-1.5 text-[12px] text-ink-3">{note}</div>
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <Note>
        One request gives a weak signal, so a per-request filter flags a large number of normal requests. The watchdog
        adds the signals of an account before it decides.
      </Note>
    </Panel>
  );
}

function Personas({ metrics }: { metrics: unknown }) {
  const personas = dict(at(metrics, "per_persona"));
  if (!personas) return null;
  const keys = Object.keys(personas).sort((a, b) => Number(ATTACK_PERSONAS.has(b)) - Number(ATTACK_PERSONAS.has(a)));
  return (
    <Panel>
      <PanelHeader title="Accounts by persona" detail="true labels, totals only" />
      <DataTable
        columns={[
          { label: "Persona" },
          { label: "Kind" },
          { label: "Accounts", align: "right" },
          { label: "Blocked by the account score", align: "right" },
          { label: "Blocked after campaigns", align: "right" },
          { label: "Review or block", align: "right" },
          { label: "Share blocked" },
        ]}
        rows={keys.map((key) => {
          const persona = dict(personas[key]) ?? {};
          return [
            personaLabel(key),
            <span className="text-ink-3">{ATTACK_PERSONAS.has(key) ? "Attack" : "Normal"}</span>,
            formatNumber(num(persona.accounts)),
            formatNumber(num(persona.account_level_block)),
            formatNumber(num(persona.final_block)),
            formatNumber(num(persona.final_review_or_block)),
            <ShareBar share={num(persona.rate_final_block)} />,
          ];
        })}
      />
      <Note>
        "Blocked by the account score" uses only the score of the account. "Blocked after campaigns" adds the accounts
        that the watchdog blocks because they belong to a blocked campaign.
      </Note>
    </Panel>
  );
}

function TimeToBlock({ metrics }: { metrics: unknown }) {
  const personas = dict(at(metrics, "time_to_block", "per_persona"));
  const leakage = dict(at(metrics, "time_to_block", "leakage", "by_persona"));
  const leakageNote = at(metrics, "time_to_block", "leakage", "note");
  if (!personas && !leakage) return null;
  const leakageColumns = leakage ? [...new Set(Object.values(leakage).flatMap((row) => Object.keys(dict(row) ?? {})))] : [];
  return (
    <div className="grid gap-4 xl:grid-cols-2">
      {personas && (
        <Panel>
          <PanelHeader title="Time to block" detail="attack personas" />
          <DataTable
            columns={[
              { label: "Persona" },
              { label: "Accounts", align: "right" },
              { label: "Blocked", align: "right" },
              { label: "Median requests", align: "right" },
              { label: "Median minutes (sim)", align: "right" },
            ]}
            rows={Object.entries(personas).map(([key, value]) => {
              const row = dict(value) ?? {};
              return [
                personaLabel(key),
                formatNumber(num(row.accounts)),
                formatNumber(num(row.blocked)),
                formatNumber(num(row.median_requests_to_block)),
                formatNumber(num(row.median_sim_minutes_to_block)),
              ];
            })}
          />
        </Panel>
      )}
      {leakage && (
        <Panel>
          <PanelHeader title="Complete trajectories that got through" detail="what each persona received" />
          <DataTable
            columns={[{ label: "Persona" }, ...leakageColumns.map((key) => ({ label: humanizeKey(key), align: "right" as const }))]}
            rows={Object.entries(leakage).map(([key, value]) => [
              personaLabel(key),
              ...leakageColumns.map((column) =>
                (column.endsWith("_share") ? formatPercent : formatNumber)(num(dict(value)?.[column])),
              ),
            ])}
          />
          {typeof leakageNote === "string" && <Note>{leakageNote}</Note>}
        </Panel>
      )}
    </div>
  );
}

function DetectorChecks({ metrics }: { metrics: unknown }) {
  const bank = dict(at(metrics, "elicitation_bank_b"));
  const env = dict(at(metrics, "fake_env"));
  if (!bank && !env) return null;
  const bankRows = bank
    ? ([
        ["Classifier", dict(bank.classifier)],
        ["Keyword baseline", dict(bank.keyword_baseline)],
      ] as const)
    : [];
  const envRows = env
    ? ([
        ["Fake-environment accounts", dict(env.fake_env)],
        ["Other accounts", dict(env.others)],
      ] as const)
    : [];
  const realism = num(env?.realism_fpr_on_real_swechat_holdout);
  return (
    <div className="grid gap-4 xl:grid-cols-2">
      {bank && (
        <Panel>
          <PanelHeader title="D4 Elicitation" detail="on phrases not seen in training" />
          <DataTable
            columns={[
              { label: "Model" },
              { label: "Precision", align: "right" },
              { label: "Recall", align: "right" },
              { label: "False positive rate", align: "right" },
            ]}
            rows={bankRows.map(([label, row]) => [
              label,
              formatPercent(num(row?.precision)),
              formatPercent(num(row?.recall)),
              formatPercent(num(row?.fpr)),
            ])}
          />
        </Panel>
      )}
      {env && (
        <Panel>
          <PanelHeader title="D6 Environment" detail="contradictions in tool outputs" />
          <DataTable
            columns={[
              { label: "Group" },
              { label: "Per 100 tool results", align: "right" },
              { label: "Sessions affected", align: "right" },
              { label: "Realism score", align: "right" },
            ]}
            rows={envRows.map(([label, row]) => [
              label,
              formatNumber(num(row?.d6_contradictions_per_100)),
              formatPercent(num(row?.d6_contradiction_session_frac)),
              formatNumber(num(row?.d6_fake_prob_mean)),
            ])}
          />
          {realism !== undefined && (
            <Note>
              The realism classifier flags {formatPercent(realism)} of{" "}
              {formatNumber(num(env.realism_real_outputs_n))} real tool outputs in the holdout.
            </Note>
          )}
        </Panel>
      )}
    </div>
  );
}

const CONTROL_LABEL: Record<string, string> = {
  scaffold_denylist: "Scaffold denylist",
  format_probe: "Format probe: can formatting alone tell the data source?",
  assistant_prose_probe: "Assistant-prose probe",
  cross_harness: "Cross-harness: the same attack on data from another agent harness",
  source_reliance: "Source reliance: does the score follow the data source?",
  label_permutation: "Label permutation",
};

function ControlStatus({ pass }: { pass: unknown }) {
  if (pass === true) {
    return (
      <span className="inline-flex h-5 items-center gap-1 rounded-full bg-st-normal/15 px-2 text-[11.5px] font-medium text-st-normal-ink">
        <Check className="size-3" aria-hidden="true" />
        Pass
      </span>
    );
  }
  if (pass === false) {
    return (
      <span className="inline-flex h-5 items-center gap-1 rounded-full bg-st-block/15 px-2 text-[11.5px] font-medium text-st-block-ink">
        <X className="size-3" aria-hidden="true" />
        Fail
      </span>
    );
  }
  return (
    <span className="inline-flex h-5 items-center gap-1 rounded-full bg-hover px-2 text-[11.5px] font-medium text-ink-2">
      <Info className="size-3" aria-hidden="true" />
      Info
    </span>
  );
}

function Controls({ controls }: { controls: unknown }) {
  const all = dict(controls);
  if (!all) return null;
  const keys = Object.keys(all).filter((key) => key !== "feature_ablation" && dict(all[key]));
  if (!keys.length) return null;
  return (
    <Panel>
      <PanelHeader title="Leakage controls" detail="checks that the detectors do not learn the data source" />
      <ul className="divide-y divide-line">
        {keys.map((key) => {
          const control = dict(all[key]) ?? {};
          const numbers = Object.entries(control).filter(([name, value]) => name !== "pass" && typeof value === "number");
          return (
            <li key={key} className="grid gap-x-6 gap-y-2 px-4 py-3 lg:grid-cols-[5rem_minmax(0,1fr)]">
              <div>
                <ControlStatus pass={control.pass} />
              </div>
              <div className="min-w-0">
                <h3 className="text-[13px] font-medium">{CONTROL_LABEL[key] ?? humanizeKey(key)}</h3>
                {numbers.length > 0 && (
                  <dl className="mt-1.5 flex flex-wrap gap-x-5 gap-y-1">
                    {numbers.map(([name, value]) => (
                      <div key={name} className="flex items-baseline gap-1.5">
                        <dt className="text-[12px] text-ink-3">{humanizeKey(name)}</dt>
                        <dd className="font-mono text-[12px] tabular-nums">{formatNumber(value)}</dd>
                      </div>
                    ))}
                  </dl>
                )}
                {typeof control.note === "string" && (
                  <p className="mt-1.5 max-w-[86ch] text-[12px] leading-relaxed text-ink-3">{control.note}</p>
                )}
              </div>
            </li>
          );
        })}
      </ul>
    </Panel>
  );
}

function Ablation({ controls }: { controls: unknown }) {
  const variants = dict(at(controls, "feature_ablation", "variants"));
  const note = at(controls, "feature_ablation", "note");
  if (!variants) return null;
  return (
    <Panel>
      <PanelHeader title="Feature-family ablation" detail="what the result depends on" />
      <DataTable
        columns={[
          { label: "Feature set" },
          { label: "Features", align: "right" },
          { label: "Holdout AUC" },
          { label: "Recall at block", align: "right" },
          { label: "False positive rate at block", align: "right" },
        ]}
        rows={Object.entries(variants).map(([key, value]) => {
          const row = dict(value) ?? {};
          const auc = num(row.holdout_auc);
          return [
            <span className="font-mono text-[12px]">{key}</span>,
            formatNumber(num(row.n_features)),
            <span className="flex items-center gap-2">
              <span className="h-1.5 w-24 overflow-hidden rounded-full bg-hover">
                {/* AUC 0.5 is chance, so the bar starts there. */}
                <span
                  className="block h-full rounded-full bg-ink-2"
                  style={{ width: `${Math.max(0, Math.min(1, ((auc ?? 0.5) - 0.5) / 0.5)) * 100}%` }}
                />
              </span>
              <span className="font-mono text-[12px] tabular-nums">{formatNumber(auc)}</span>
            </span>,
            formatPercent(num(row.holdout_recall_at_block)),
            formatPercent(num(row.holdout_fpr_at_block)),
          ];
        })}
      />
      <Note>
        The AUC bar starts at 0.5, which is chance.{typeof note === "string" ? ` ${note}` : ""}
      </Note>
    </Panel>
  );
}

function Compute({ report }: { report: Dict }) {
  const compute = dict(report.compute) ?? dict(at(report.metrics, "compute"));
  const load = dict(report.load) ?? dict(at(report.metrics, "compute", "load"));
  const cost = num(at(report.metrics, "compute", "llm_cost", "total_cost_usd"));
  if (!compute && !load && cost === undefined) return null;
  const train = dict(compute?.train);
  const stages = Object.entries(train ?? {}).filter(([, value]) => dict(value));
  const seconds = (value: unknown) => (num(value) === undefined ? undefined : `${formatNumber(num(value))} s`);
  const ms = (value: unknown) => (num(value) === undefined ? undefined : `${formatNumber(num(value))} ms`);
  const perSecond = (value: unknown) => (num(value) === undefined ? undefined : `${formatNumber(num(value))} / s`);
  return (
    <Panel>
      <PanelHeader title="Compute and cost" />
      <Facts
        items={[
          ["Training, wall time", seconds(train?.train_total_s)],
          ["Training, CPU time", seconds(train?.train_total_cpu_s)],
          ["Evaluation, requests", perSecond(at(compute, "eval", "requests_per_s"))],
          ["Gateway load test, requests", perSecond(load?.rps)],
          ["Gateway handling time, p95", ms(load?.lab_handle_p95_ms)],
          ["OpenRouter cost, total", cost === undefined ? undefined : `$${cost.toFixed(2)}`],
        ]}
      />
      {stages.length > 0 && (
        <div className="border-t border-line">
          <DataTable
            columns={[
              { label: "Training stage" },
              { label: "Wall time (s)", align: "right" },
              { label: "CPU time (s)", align: "right" },
              { label: "Peak memory (MB)", align: "right" },
            ]}
            rows={stages.map(([key, value]) => {
              const stage = dict(value) ?? {};
              return [
                <span className="font-mono text-[12px]">{key}</span>,
                formatNumber(num(stage.wall_s)),
                formatNumber(num(stage.cpu_s)),
                formatNumber(num(stage.peak_rss_mb)),
              ];
            })}
          />
        </div>
      )}
    </Panel>
  );
}

function Figures({ images }: { images: unknown[] }) {
  const [open, setOpen] = useState<string | null>(null);
  // Only paths of this server are accepted.
  const safe = images.filter(
    (url): url is string => typeof url === "string" && url.startsWith("/") && !url.startsWith("//"),
  );
  if (!safe.length) return null;
  const nameOf = (url: string) => humanizeKey((url.split("/").pop() ?? url).replace(/\.png$/i, ""));
  return (
    <Panel>
      <PanelHeader title="Figures" detail="select a figure to enlarge it" />
      <div className="grid gap-4 p-4 md:grid-cols-2 xl:grid-cols-3">
        {safe.map((url) => (
          <figure key={url}>
            <button
              type="button"
              onClick={() => setOpen(url)}
              className="block w-full overflow-hidden rounded-md border border-line bg-white"
              aria-label={`Enlarge the figure: ${nameOf(url)}`}
            >
              <img src={url} alt={nameOf(url)} loading="lazy" className="block h-auto w-full" />
            </button>
            <figcaption className="mt-1.5 text-[12px] text-ink-3">{nameOf(url)}</figcaption>
          </figure>
        ))}
      </div>
      <Dialog open={open !== null} onOpenChange={(isOpen) => !isOpen && setOpen(null)}>
        <DialogContent className="max-h-[92dvh] gap-3 overflow-auto border-line-strong bg-panel sm:max-w-5xl">
          <DialogTitle className="text-[14px]">{open ? nameOf(open) : "Figure"}</DialogTitle>
          <DialogDescription className="sr-only">A figure from the offline evaluation, enlarged.</DialogDescription>
          {open && <img src={open} alt={nameOf(open)} className="h-auto w-full rounded-md bg-white" />}
        </DialogContent>
      </Dialog>
    </Panel>
  );
}

// ---------- every value, as it is in the report files ----------

function isScalar(value: unknown): boolean {
  return value === null || ["string", "number", "boolean"].includes(typeof value);
}

function scalarText(value: unknown): string {
  if (value === null || value === undefined) return "–";
  if (typeof value === "number") return formatNumber(value);
  if (typeof value === "boolean") return value ? "yes" : "no";
  return String(value);
}

function JsonTree({ value, depth = 0 }: { value: unknown; depth?: number }) {
  if (isScalar(value) || value === undefined) return <span className="font-mono text-[12px]">{scalarText(value)}</span>;
  if (depth > 6) return <code className="font-mono text-[11.5px] text-ink-3">{JSON.stringify(value)}</code>;
  if (Array.isArray(value)) {
    if (value.length === 0) return <span className="text-ink-4">empty</span>;
    if (value.every(isScalar)) return <span className="font-mono text-[12px]">{value.map(scalarText).join(", ")}</span>;
    return (
      <ol className="grid gap-1">
        {value.map((item, index) => (
          <li key={index} className="border-l border-line pl-3">
            <JsonTree value={item} depth={depth + 1} />
          </li>
        ))}
      </ol>
    );
  }
  const entries = Object.entries(value as Dict);
  if (entries.length === 0) return <span className="text-ink-4">empty</span>;
  return (
    <dl className="grid gap-1">
      {entries.map(([key, item]) =>
        isScalar(item) ? (
          <div key={key} className="flex flex-wrap items-baseline gap-x-3">
            <dt className="font-mono text-[12px] text-ink-3">{key}</dt>
            <dd className="min-w-0 break-words">
              <JsonTree value={item} depth={depth + 1} />
            </dd>
          </div>
        ) : (
          <div key={key}>
            <dt className="font-mono text-[12px] text-ink-3">{key}</dt>
            <dd className="mt-1 ml-1 border-l border-line pl-3">
              <JsonTree value={item} depth={depth + 1} />
            </dd>
          </div>
        ),
      )}
    </dl>
  );
}

function RawReports({ report }: { report: Dict }) {
  const sections = Object.keys(report).filter((key) => key !== "images");
  if (!sections.length) return null;
  return (
    <Panel>
      <PanelHeader title="All report values" detail="as the offline evaluation wrote them" />
      <div className="divide-y divide-line">
        {sections.map((key) => (
          <details key={key} className="group px-4 py-2">
            <summary className="cursor-pointer py-1 font-mono text-[12.5px] select-none">{key}.json</summary>
            <div className="overflow-x-auto py-2">
              <JsonTree value={report[key]} />
            </div>
          </details>
        ))}
      </div>
    </Panel>
  );
}

// ---------- the page ----------

function Scenario({ metrics }: { metrics: unknown }) {
  const scenario = dict(at(metrics, "scenario"));
  if (!scenario) return null;
  return (
    <p className="max-w-[80ch] text-[13px] leading-relaxed text-ink-2">
      Results on the <strong className="font-semibold text-foreground">{String(scenario.split ?? "holdout")}</strong>{" "}
      scenario: {formatNumber(num(scenario.requests))} requests from {formatNumber(num(scenario.accounts))} accounts,
      of which {formatNumber(num(scenario.scored_accounts))} have enough requests to score (scale{" "}
      {String(scenario.scale ?? "–")}, seed {String(scenario.seed ?? "–")}).
    </p>
  );
}

export function Evaluation() {
  const [helpOpen, toggleHelp] = useHelp("eval");
  const query = useEvaluation();
  const report = query.data as Dict | undefined;
  const empty = report && Object.keys(report).every((key) => key === "images" || !dict(report[key]) || !Object.keys(dict(report[key]) ?? {}).length);

  return (
    <>
      <PageHeader
        title="Evaluation"
        actions={
          <>
            <Button
              variant="outline"
              size="sm"
              className="rounded-full border-line-strong bg-transparent"
              disabled={query.isFetching}
              onClick={() => {
                void query.refetch();
              }}
            >
              <RefreshCw className={cn(query.isFetching && "animate-spin")} />
              Reload
            </Button>
            <HelpButton open={helpOpen} onToggle={toggleHelp} />
          </>
        }
      />
      <PageBody>
        <HelpNote open={helpOpen} onClose={toggleHelp}>
          These numbers and figures come from the offline evaluation (<code className="font-mono">scripts/eval.sh</code>
          ), not from the live traffic. Only this page uses the true labels of the accounts, and it shows them only as
          totals. Select Reload after a new evaluation run.
        </HelpNote>

        {query.isPending && <Quiet>Loading the evaluation.</Quiet>}
        {query.isError && !report && (
          <Problem>
            The evaluation is not available. {query.error.message} Run{" "}
            <code className="font-mono">scripts/eval.sh</code>, then select Reload.
          </Problem>
        )}
        {report && empty && (
          <Problem>
            The report files are empty. Run <code className="font-mono">scripts/eval.sh</code>, then select Reload.
          </Problem>
        )}
        {report && !empty && (
          <>
            <Scenario metrics={report.metrics} />
            <Comparison metrics={report.metrics} />
            <Personas metrics={report.metrics} />
            <TimeToBlock metrics={report.metrics} />
            <DetectorChecks metrics={report.metrics} />
            <Controls controls={report.controls} />
            <Ablation controls={report.controls} />
            <Compute report={report} />
            <Figures images={Array.isArray(report.images) ? report.images : []} />
            <RawReports report={report} />
          </>
        )}
      </PageBody>
    </>
  );
}
