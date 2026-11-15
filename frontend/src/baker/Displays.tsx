// Three displays with simple content: Recent activity, Active metrics, and the Evaluation report.

import { DisplayFrame, Note, Stamp, emptyText, plural, toneOf } from "@/baker/parts";
import type { FeedItem } from "@/hooks/useStream";
import { useApp } from "@/lib/app-context";
import { formatCount, formatNumber, formatPercent, formatScore, formatSimDateTime, formatSimTime } from "@/lib/format";
import { routeHref } from "@/lib/router";
import { STATES, STATE_META } from "@/lib/states";
import { Evaluation } from "@/pages/Evaluation";

// ---------- A. Recent activity ----------

function Telegram({ item }: { item: FeedItem }) {
  const time = formatSimTime(item.simTime);
  if (item.kind === "campaign") {
    return (
      <li>
        <a className="bk-telegram bk-perf-left" href={routeHref({ page: "campaigns", id: item.event.id })}>
          <span className="bk-telegram__time">{time}</span>
          <span className="bk-telegram__text">
            <b>{item.event.id}</b> New campaign. {plural(item.event.size, "account", "accounts")} are linked. Campaign
            score {formatNumber(item.event.score)}.
          </span>
          <Stamp state={item.event.state} />
        </a>
      </li>
    );
  }
  const event = item.event;
  return (
    <li>
      <a className={`bk-telegram bk-perf-left bk-telegram--${toneOf(event.state)}`} href={routeHref({ page: "account", id: event.account_id })}>
        <span className="bk-telegram__time">{time}</span>
        <span className="bk-telegram__text">
          <b>{event.pseudonym}</b> {item.kind === "block" ? "The gateway now refuses this account." : "The watchdog flagged this account."}{" "}
          Score {formatScore(event.score)}.{event.reason && <em> Reason: {event.reason}</em>}
        </span>
        <Stamp state={event.state} />
      </a>
    </li>
  );
}

export function ActivityDisplay() {
  const { stream } = useApp();
  return (
    <DisplayFrame
      title="Recent activity"
      note="Flags, blocks, and new campaigns. The newest is first. The list starts when you open this page."
      back={{ page: "live" }}
    >
      {stream.feed.length ? (
        <ol className="bk-telegrams" aria-live="polite">
          {stream.feed.map((item) => (
            <Telegram key={item.key} item={item} />
          ))}
        </ol>
      ) : (
        <Note>
          {emptyText(
            stream.online,
            "No new flags since you opened this page. A new flag, block, or campaign shows here when the watchdog decides it. The accounts list shows all accounts that are blocked now.",
          )}
        </Note>
      )}
    </DisplayFrame>
  );
}

// ---------- D. Active metrics ----------

function Figure({ label, value, note, tone, wide }: { label: string; value: string; note?: string; tone?: string; wide?: boolean }) {
  return (
    <div className={`bk-index ${tone ? `bk-index--${tone}` : ""} ${wide ? "bk-index--wide" : ""}`}>
      <dt>{label}</dt>
      <dd>{value}</dd>
      {note && <p>{note}</p>}
    </div>
  );
}

export function MetricsDisplay() {
  const { stream } = useApp();
  const summary = stream.summary;
  const total = summary ? summary.served + summary.blocked : 0;
  return (
    <DisplayFrame title="Active metrics" note="Live numbers from the watchdog. They change each second." back={{ page: "live" }}>
      {!summary ? (
        <Note>{emptyText(stream.online, "Waiting for the first numbers from the watchdog.")}</Note>
      ) : (
        <>
          <h2 className="bk-heading">Requests</h2>
          <dl className="bk-indexes">
            <Figure label="Requests each second" value={formatNumber(Math.round(summary.rps * 10) / 10)} note="Real time, last 5 seconds" />
            <Figure label="Requests served" value={formatCount(summary.served)} note="The gateway answered these" />
            <Figure label="Requests blocked" value={formatCount(summary.blocked)} note="The gateway refused these" tone="red" />
            <Figure label="Share blocked" value={total ? formatPercent(summary.blocked / total) : "–"} note="Blocked of all requests" tone="red" />
          </dl>
          <h2 className="bk-heading">Accounts</h2>
          <dl className="bk-indexes">
            {STATES.map((state) => (
              <Figure
                key={state}
                label={STATE_META[state].label}
                value={formatNumber(summary.accounts?.[state] ?? 0)}
                note={STATE_META[state].meaning}
                tone={toneOf(state)}
              />
            ))}
            <Figure label="Campaigns found" value={formatNumber(summary.campaigns)} note="Groups of linked accounts" />
          </dl>
          <h2 className="bk-heading">Watchdog</h2>
          <dl className="bk-indexes">
            <Figure label="Events in the queue" value={formatNumber(summary.queue_depth)} note="Requests that wait for the watchdog" />
            <Figure label="Events dropped" value={formatNumber(summary.dropped)} note="Requests that the watchdog did not read" />
            <Figure label="Simulated time" value={formatSimDateTime(summary.sim_time).slice(0, 16) || "–"} note="UTC. It runs faster than real time." wide />
          </dl>
        </>
      )}
    </DisplayFrame>
  );
}

// ---------- F. Evaluation ----------

export function EvaluationDisplay() {
  return (
    <DisplayFrame title="Evaluation" note="The case report: results of the offline evaluation, not of the live traffic." back={{ page: "live" }}>
      <div className="bk-paper bk-reuse bk-report">
        <Evaluation />
      </div>
    </DisplayFrame>
  );
}
