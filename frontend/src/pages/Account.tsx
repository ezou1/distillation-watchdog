import { ChevronDown, ChevronUp } from "lucide-react";
import { useCallback } from "react";

import { AccountView } from "@/components/AccountView";
import { CaseFileButton } from "@/components/CaseFile";
import { HelpButton, HelpNote, useHelp } from "@/components/Notes";
import { PageBody, PageHeader } from "@/components/PageHeader";
import { Button } from "@/components/ui/button";
import { useAccount, useAccounts } from "@/hooks/queries";
import { useKey } from "@/hooks/useListKeys";
import { navigate, routeHref } from "@/lib/router";

const PAGER_LIMIT = 200;

export function Account({ id }: { id: string }) {
  const [helpOpen, toggleHelp] = useHelp("account");
  const account = useAccount(id);
  // The pager steps through the accounts in score order, like the Live table.
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
    <>
      <PageHeader
        trail={
          <a href={routeHref({ page: "live" })} className="underline-offset-2 hover:text-foreground hover:underline">
            Accounts
          </a>
        }
        title={<span className="font-mono font-medium">{pseudonym}</span>}
        actions={
          <>
            {index >= 0 && (
              <div className="flex items-center gap-1">
                <span className="mr-1 font-mono text-[12px] text-ink-3 tabular-nums">
                  {index + 1} / {list.length}
                </span>
                <Button
                  variant="ghost"
                  size="icon-sm"
                  onClick={goPrevious}
                  disabled={!previous}
                  aria-label="Previous account (K)"
                  title="Previous account (K)"
                >
                  <ChevronUp />
                </Button>
                <Button
                  variant="ghost"
                  size="icon-sm"
                  onClick={goNext}
                  disabled={!next}
                  aria-label="Next account (J)"
                  title="Next account (J)"
                >
                  <ChevronDown />
                </Button>
              </div>
            )}
            <CaseFileButton kind="account" id={id} />
            <HelpButton open={helpOpen} onToggle={toggleHelp} />
          </>
        }
      />
      <PageBody>
        <HelpNote open={helpOpen} onClose={toggleHelp}>
          The bars show what pushes the score of this account up or down. The percentile compares each feature with
          all accounts: a value near 100 or near 0 is unusual. Select an evidence item to see the exact request. The
          case file is a short report in which each claim cites evidence.
        </HelpNote>
        <AccountView key={id} id={id} />
      </PageBody>
    </>
  );
}
