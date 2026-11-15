import { Stamp } from "@/baker/parts";
import { CARDS } from "@/baker/Board";
import { CommandDialog, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList } from "@/components/ui/command";
import { useAccounts, useCampaigns } from "@/hooks/queries";
import { formatScore } from "@/lib/format";
import { navigate, type Route } from "@/lib/router";

const ACCOUNT_LIMIT = 200;

/** Go to a display, an account, or a campaign. Opened with Ctrl+K, Cmd+K, or the magnifying glass. */
export function Search({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const accounts = useAccounts({ limit: ACCOUNT_LIMIT }, open);
  const campaigns = useCampaigns(open);
  const go = (route: Route) => {
    onOpenChange(false);
    navigate(route);
  };

  return (
    <CommandDialog
      open={open}
      onOpenChange={onOpenChange}
      title="Search"
      description="Search for a display, an account, or a campaign."
      showCloseButton={false}
      className="border-line-strong bg-raised sm:max-w-xl"
    >
      <CommandInput placeholder="Search for a display, an account, or a campaign" />
      <CommandList className="max-h-[60dvh]">
        <CommandEmpty className="py-6 text-center text-ink-3">Nothing matches. Search by account name, campaign ID, or state.</CommandEmpty>
        <CommandGroup heading="Displays">
          <CommandItem value="board home chart" onSelect={() => go({ page: "live" })}>
            The board
          </CommandItem>
          {CARDS.map((card) => (
            <CommandItem key={card.id} value={`display ${card.title}`} onSelect={() => go(card.route)}>
              {card.title}
            </CommandItem>
          ))}
          <CommandItem value="display evaluation case report" onSelect={() => go({ page: "eval" })}>
            Evaluation
          </CommandItem>
        </CommandGroup>
        {(campaigns.data?.length ?? 0) > 0 && (
          <CommandGroup heading="Campaigns">
            {campaigns.data?.map((campaign) => (
              <CommandItem
                key={campaign.id}
                value={`campaign ${campaign.id} ${campaign.state}`}
                onSelect={() => go({ page: "campaigns", id: campaign.id })}
              >
                <span className="font-mono">{campaign.id}</span>
                <Stamp state={campaign.state} />
                <span className="ml-auto text-ink-3">{campaign.size} accounts</span>
              </CommandItem>
            ))}
          </CommandGroup>
        )}
        {(accounts.data?.length ?? 0) > 0 && (
          <CommandGroup heading="Accounts">
            {accounts.data?.map((account) => (
              <CommandItem
                key={account.id}
                value={`account ${account.pseudonym} ${account.state} ${account.you ? "you" : ""}`}
                onSelect={() => go({ page: "account", id: account.id })}
              >
                <span className="font-mono">{account.pseudonym}</span>
                <Stamp state={account.state} />
                <span className="ml-auto font-mono text-ink-3 tabular-nums">{formatScore(account.score)}</span>
              </CommandItem>
            ))}
          </CommandGroup>
        )}
      </CommandList>
    </CommandDialog>
  );
}
