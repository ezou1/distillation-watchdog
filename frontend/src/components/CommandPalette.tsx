import { Activity, FlaskConical, Moon, Sun, Waypoints } from "lucide-react";

import { StateBadge } from "@/components/StateBadge";
import {
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import { useAccounts, useCampaigns } from "@/hooks/queries";
import { formatScore } from "@/lib/format";
import { navigate, type Route } from "@/lib/router";
import { setTheme, useTheme } from "@/lib/theme";

const ACCOUNT_LIMIT = 200;

/** Jump to a page, an account or a campaign. Opened with Ctrl+K or Cmd+K. */
export function CommandPalette({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const theme = useTheme();
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
      title="Go to"
      description="Search for a page, an account or a campaign."
      showCloseButton={false}
      className="border-line-strong bg-raised sm:max-w-xl"
    >
      <CommandInput placeholder="Go to a page, an account or a campaign" />
      <CommandList className="max-h-[60dvh]">
        <CommandEmpty className="py-6 text-center text-[12.5px] text-ink-3">
          Nothing matches. Search by pseudonym, campaign ID or state.
        </CommandEmpty>
        <CommandGroup heading="Pages">
          <CommandItem value="page live accounts" onSelect={() => go({ page: "live" })}>
            <Activity />
            Live
          </CommandItem>
          <CommandItem value="page campaigns graph" onSelect={() => go({ page: "campaigns" })}>
            <Waypoints />
            Campaigns
          </CommandItem>
          <CommandItem value="page evaluation results" onSelect={() => go({ page: "eval" })}>
            <FlaskConical />
            Evaluation
          </CommandItem>
          <CommandItem
            value="theme light dark"
            onSelect={() => {
              setTheme(theme === "dark" ? "light" : "dark");
              onOpenChange(false);
            }}
          >
            {theme === "dark" ? <Sun /> : <Moon />}
            Use the {theme === "dark" ? "light" : "dark"} theme
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
                <span className="font-mono text-[12.5px]">{campaign.id}</span>
                <StateBadge state={campaign.state} />
                <span className="ml-auto text-[12px] text-ink-3">{campaign.size} accounts</span>
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
                <span className="font-mono text-[12.5px]">{account.pseudonym}</span>
                <StateBadge state={account.state} />
                <span className="ml-auto font-mono text-[12px] text-ink-3 tabular-nums">
                  {formatScore(account.score)}
                </span>
              </CommandItem>
            ))}
          </CommandGroup>
        )}
      </CommandList>
    </CommandDialog>
  );
}
