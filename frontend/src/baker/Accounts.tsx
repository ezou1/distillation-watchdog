// B. Accounts list (a ledger page) and the account dossier (a folder).

import { useCallback, useState } from "react";

import type { AccountSort, AccountState } from "@/api";
import { DisplayFrame, Note, Stamp, emptyText, reasonWords, useZooming } from "@/baker/parts";
import { AccountView } from "@/components/AccountView";
import { CaseFileButton } from "@/components/CaseFile";
import { Sparkline } from "@/components/Sparkline";
import { useAccount, useAccounts } from "@/hooks/queries";
import { useKey, useListKeys } from "@/hooks/useListKeys";
import { useApp } from "@/lib/app-context";
import { formatNumber, formatScore } from "@/lib/format";
import { navigate, routeHref } from "@/lib/router";
import { STATES, STATE_META } from "@/lib/states";

const LIMIT = 100;
/** Lines drawn while the display zooms in. The other lines come when the zoom ends, so the zoom stays smooth. */
const LINES_IN_ZOOM = 14;
const PAGER_LIMIT = 200;

const SORTS: Array<{ value: AccountSort; label: string }> = [
  { value: "score", label: "Highest score first" },
  { value: "requests", label: "Most requests first" },
  { value: "last_seen", label: "Last seen first" },
];

function ScoreMark({ score }: { score: number | null }) {
  const known = typeof score === "number" && Number.isFinite(score);
  return (
    <span className="bk-score">
      <b>{formatScore(score)}</b>
      <span className="bk-score__track" aria-hidden="true">
        {known && <span style={{ width: `${Math.max(0, Math.min(1, score)) * 100}%` }} />}
      </span>
    </span>
  );
}

export function AccountsDisplay() {
  const { stream } = useApp();
  const [state, setState] = useState<AccountState | "">("");
  const [sort, setSort] = useState<AccountSort>("score");
  const accounts = useAccounts({ state, sort, limit: LIMIT });
  const zooming = useZooming();
  const all = accounts.data ?? [];
  const rows = zooming ? all.slice(0, LINES_IN_ZOOM) : all;
  const open = useCallback(
    (index: number) => {
      const row = rows[index];
      if (row) navigate({ page: "account", id: row.id });
    },
    [rows],
  );
  const [active, setActive] = useListKeys(rows.length, open);
  const counts = stream.summary?.accounts;

  return (
    <DisplayFrame
      title="Accounts list"
      note="One line for each account. The score goes from 0 to 1. A high score means more signs of distillation."
      back={{ page: "live" }}
    >
      <div className="bk-paper bk-ledger">
        <div className="bk-ledger__tools">
          <div className="bk-tabs" role="group" aria-label="Show accounts in one state">
            <button type="button" aria-pressed={state === ""} onClick={() => setState("")}>
              All
            </button>
            {STATES.map((option) => (
              <button key={option} type="button" aria-pressed={state === option} onClick={() => setState(option)}>
                {STATE_META[option].label}
                {typeof counts?.[option] === "number" && <span>{formatNumber(counts[option])}</span>}
              </button>
            ))}
          </div>
          <label className="bk-select">
            Order
            <select value={sort} onChange={(event) => setSort(event.target.value as AccountSort)}>
              {SORTS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>
        </div>

        {accounts.isError && !rows.length ? (
          <Note>
            {stream.online === false ? emptyText(false, "") : `The accounts list did not load. ${accounts.error.message}`}
          </Note>
        ) : accounts.isPending ? (
          <Note>Loading the accounts.</Note>
        ) : rows.length === 0 ? (
          <Note>
            {state
              ? "No account is in this state now."
              : emptyText(stream.online, "No requests yet. Start a traffic generator.")}
          </Note>
        ) : (
          <div className="bk-ledger__scroll">
            <table className="bk-table">
              <thead>
                <tr>
                  <th>Account</th>
                  <th>State</th>
                  <th>Score</th>
                  <th>Score history</th>
                  <th className="bk-num">Requests</th>
                  <th>Campaign</th>
                  <th>Main signs</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row, index) => (
                  <tr
                    key={row.id}
                    data-row-index={index}
                    aria-selected={index === active}
                    onClick={() => {
                      setActive(index);
                      navigate({ page: "account", id: row.id });
                    }}
                  >
                    <td>
                      <a href={routeHref({ page: "account", id: row.id })} onClick={(event) => event.stopPropagation()}>
                        {row.pseudonym}
                      </a>
                      {row.you && <span className="bk-you">You</span>}
                    </td>
                    <td>
                      <Stamp state={row.state} />
                    </td>
                    <td>
                      <ScoreMark score={row.score} />
                    </td>
                    <td>
                      <Sparkline history={row.history} state={row.state} />
                    </td>
                    <td className="bk-num">{formatNumber(row.requests)}</td>
                    <td>
                      {row.campaign_id ? (
                        <a href={routeHref({ page: "campaigns", id: row.campaign_id })} onClick={(event) => event.stopPropagation()}>
                          {row.campaign_id}
                        </a>
                      ) : (
                        "–"
                      )}
                    </td>
                    <td className="bk-signs">{reasonWords(row.reasons, row.rules_level).join(", ") || "–"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <p className="bk-ledger__foot">
          {rows.length === LIMIT ? `The first ${LIMIT} accounts. ` : ""}
          Select a line to open the account. Keys: J and K move, Enter opens.
        </p>
      </div>
    </DisplayFrame>
  );
}

export function DossierDisplay({ id }: { id: string }) {
  const account = useAccount(id);
  // The pager steps through the accounts in score order, like the accounts list.
  const list = useAccounts({ limit: PAGER_LIMIT }).data ?? [];
  const index = list.findIndex((row) => row.id === id);
  const previous = index > 0 ? list[index - 1] : undefined;
  const next = index >= 0 ? list[index + 1] : undefined;
  const goPrevious = useCallback(() => previous && navigate({ page: "account", id: previous.id }), [previous]);
  const goNext = useCallback(() => next && navigate({ page: "account", id: next.id }), [next]);
  useKey("k", goPrevious, Boolean(previous));
  useKey("j", goNext, Boolean(next));

  const pseudonym = account.data?.pseudonym ?? list[index]?.pseudonym ?? "Account";

  return (
    <DisplayFrame
      title={<span className="bk-typed">{pseudonym}</span>}
      note="Account dossier. Select an evidence item to see the exact request."
      back={{ page: "accounts" }}
      backLabel="Back to the accounts list"
      actions={
        <>
          {index >= 0 && (
            <div className="bk-pager">
              <span>
                {index + 1} of {list.length}
              </span>
              <button type="button" onClick={goPrevious} disabled={!previous} title="Previous account (K)">
                Previous
              </button>
              <button type="button" onClick={goNext} disabled={!next} title="Next account (J)">
                Next
              </button>
            </div>
          )}
          <span className="bk-reuse">
            <CaseFileButton kind="account" id={id} />
          </span>
        </>
      }
    >
      <div className="bk-dossier">
        <span className="bk-dossier__tab">Dossier</span>
        <div className="bk-paper bk-reuse">
          <AccountView key={id} id={id} />
        </div>
      </div>
    </DisplayFrame>
  );
}
