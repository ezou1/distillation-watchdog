// C. Suspicious messages: the messages that the watchdog flagged, with the common ones first.
//
// No endpoint gives this list. The page builds it from documented endpoints:
//   1. the accounts in the states "block" and "review",
//   2. the evidence of each account (the items with the highest weight),
//   3. the message that each evidence item points to.
// Entries with the same message text become one entry with a count of accounts.

import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";

import { watchdog, type AccountRow, type AccountState, type CanonicalMessage } from "@/api";
import { DisplayFrame, Note, Stamp, emptyText, moreSevere, plural, toneOf, useZooming } from "@/baker/parts";
import { useApp } from "@/lib/app-context";
import { routeHref } from "@/lib/router";
import { detectorKey, detectorMeta } from "@/lib/states";

/** Limits, so the page does not send too many requests. */
const ACCOUNTS_PER_STATE = 20;
const ITEMS_PER_ACCOUNT = 4;
const PARALLEL = 4;
const REFRESH_MS = 20_000;
const EXCERPT = 260;

interface Sighting {
  evidenceId: string;
  detector: string;
  account: Pick<AccountRow, "id" | "pseudonym" | "state">;
  message: CanonicalMessage;
}

interface Entry {
  key: string;
  message: CanonicalMessage;
  text: string;
  accounts: Map<string, Sighting["account"]>;
  detectors: Set<string>;
  evidenceIds: string[];
  state: AccountState;
}

const ROLE_WORDS: Record<string, string> = {
  user: "Prompt from the user",
  assistant: "Answer of the model",
  tool_call: "Tool call",
  tool_result: "Tool output",
};

function messageText(message: CanonicalMessage): string {
  if (message.text?.trim()) return message.text.trim();
  if (message.args) return `${message.tool ?? "tool"} ${JSON.stringify(message.args)}`;
  return "";
}

function group(sightings: Sighting[]): Entry[] {
  const entries = new Map<string, Entry>();
  for (const sighting of sightings) {
    const text = messageText(sighting.message);
    if (!text) continue;
    const key = `${sighting.message.role}|${text.replace(/\s+/g, " ")}`;
    let entry = entries.get(key);
    if (!entry) {
      entry = { key, message: sighting.message, text, accounts: new Map(), detectors: new Set(), evidenceIds: [], state: sighting.account.state };
      entries.set(key, entry);
    }
    entry.accounts.set(sighting.account.id, sighting.account);
    entry.detectors.add(detectorKey(sighting.detector));
    entry.evidenceIds.push(sighting.evidenceId);
    entry.state = moreSevere(entry.state, sighting.account.state);
  }
  return [...entries.values()].sort(
    (a, b) => b.accounts.size - a.accounts.size || (a.state === b.state ? 0 : moreSevere(a.state, b.state) === a.state ? -1 : 1) || b.evidenceIds.length - a.evidenceIds.length,
  );
}

/** `enabled` is false while the display zooms in: the reading starts when the zoom ends. */
function useFlaggedMessages(enabled: boolean) {
  const client = useQueryClient();
  const [sightings, setSightings] = useState<Sighting[]>([]);
  const [progress, setProgress] = useState({ done: 0, total: 0, loading: true, error: "" });

  useEffect(() => {
    if (!enabled) return;
    let stopped = false;
    let first = true;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const run = async () => {
      const found: Sighting[] = [];
      try {
        const lists = await Promise.all(
          (["block", "review"] as const).map((state) =>
            client.fetchQuery({
              queryKey: ["accounts", state, "score", ACCOUNTS_PER_STATE],
              queryFn: () => watchdog.accounts({ state, sort: "score", limit: ACCOUNTS_PER_STATE }),
              staleTime: 5000,
            }),
          ),
        );
        const queue = lists.flatMap((list) => list.accounts ?? []);
        const total = queue.length;
        let done = 0;
        if (!stopped && first) setProgress({ done, total, loading: total > 0, error: "" });

        // A small number of workers take accounts from the queue, so only a few requests run at one time.
        const worker = async () => {
          for (let account = queue.shift(); account && !stopped; account = queue.shift()) {
            try {
              const detail = await client.fetchQuery({
                queryKey: ["account", account.id],
                queryFn: () => watchdog.account(account!.id),
                staleTime: REFRESH_MS,
              });
              const items = (detail.evidence ?? []).filter((item) => item.request_ids?.length).slice(0, ITEMS_PER_ACCOUNT);
              for (const item of items) {
                if (stopped) return;
                const evidence = await client.fetchQuery({
                  queryKey: ["evidence", item.id],
                  queryFn: () => watchdog.evidence(item.id),
                  staleTime: Infinity,
                });
                if (evidence.message) {
                  found.push({ evidenceId: item.id, detector: item.detector ?? "", account, message: evidence.message });
                }
              }
            } catch {
              // One account that does not load (for example after a reset) must not stop the others.
            }
            done += 1;
            // The first pass shows the entries as they arrive. A later pass replaces the list when it is complete.
            if (!stopped && first) {
              setSightings([...found]);
              setProgress({ done, total, loading: done < total, error: "" });
            }
          }
        };
        await Promise.all(Array.from({ length: PARALLEL }, worker));
        if (stopped) return;
        setSightings(found);
        setProgress({ done: total, total, loading: false, error: "" });
      } catch (error) {
        if (!stopped) setProgress((was) => ({ ...was, loading: false, error: error instanceof Error ? error.message : "The list did not load." }));
      }
      first = false;
      if (!stopped) timer = setTimeout(() => void run(), REFRESH_MS);
    };

    void run();
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [client, enabled]);

  return { entries: useMemo(() => group(sightings), [sightings]), progress };
}

function Clipping({ entry }: { entry: Entry }) {
  const { openEvidence } = useApp();
  const [open, setOpen] = useState(false);
  const accounts = [...entry.accounts.values()];
  const long = entry.text.length > EXCERPT;
  return (
    <li className={`bk-clipping bk-clipping--${toneOf(entry.state)}`}>
      <button type="button" className="bk-clipping__head" aria-expanded={open} onClick={() => setOpen((was) => !was)}>
        <span className="bk-clipping__meta">
          <Stamp state={entry.state} />
          <b>{accounts.length === 1 ? "Sent by 1 account" : `Sent by ${accounts.length} accounts`}</b>
          <span>{ROLE_WORDS[entry.message.role] ?? "Message"}</span>
        </span>
        <span className="bk-clipping__text">{open || !long ? entry.text : `${entry.text.slice(0, EXCERPT)}…`}</span>
        <span className="bk-clipping__more">{open ? "Hide the accounts and the reason" : "Show the accounts and the reason"}</span>
      </button>
      {open && (
        <div className="bk-clipping__detail">
          <h3>Why the watchdog flagged it</h3>
          <ul>
            {[...entry.detectors].map((key) => {
              const detector = detectorMeta(key);
              return (
                <li key={key}>
                  <b>{detector.name}.</b> {detector.signal}
                </li>
              );
            })}
          </ul>
          <h3>Accounts that sent it</h3>
          <ul className="bk-clipping__accounts">
            {accounts.map((account) => (
              <li key={account.id}>
                <a href={routeHref({ page: "account", id: account.id })}>{account.pseudonym}</a>
                <Stamp state={account.state} />
              </li>
            ))}
          </ul>
          <button type="button" className="bk-clipping__link" onClick={() => entry.evidenceIds[0] && openEvidence(entry.evidenceIds[0])}>
            Show the full request
          </button>
        </div>
      )}
    </li>
  );
}

export function MessagesDisplay() {
  const { stream } = useApp();
  const { entries, progress } = useFlaggedMessages(!useZooming());
  return (
    <DisplayFrame
      title="Suspicious messages"
      note="Messages that the watchdog flagged, from blocked and suspicious accounts. Messages that many accounts sent are first."
      back={{ page: "live" }}
    >
      {progress.error && !entries.length ? (
        <Note>{stream.online === false ? emptyText(false, "") : `The messages did not load. ${progress.error}`}</Note>
      ) : entries.length === 0 ? (
        <Note>
          {progress.loading
            ? "Reading the evidence of the flagged accounts."
            : emptyText(stream.online, "No flagged messages yet. They show here when the watchdog flags an account.")}
        </Note>
      ) : (
        <>
          <p className="bk-caption">
            {plural(entries.length, "message", "messages")} from {plural(progress.total, "flagged account", "flagged accounts")}.
            {progress.loading && ` Reading account ${progress.done + 1} of ${progress.total}.`} The page reads a maximum of{" "}
            {ACCOUNTS_PER_STATE} blocked and {ACCOUNTS_PER_STATE} suspicious accounts, and {ITEMS_PER_ACCOUNT} evidence items for
            each account.
          </p>
          <ul className="bk-clippings">
            {entries.map((entry) => (
              <Clipping key={entry.key} entry={entry} />
            ))}
          </ul>
        </>
      )}
    </DisplayFrame>
  );
}
