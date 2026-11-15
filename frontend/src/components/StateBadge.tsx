import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { stateMeta } from "@/lib/states";
import { cn } from "@/lib/utils";

/**
 * The state of an account or campaign, as a Radar verdict pill: tinted ground, colored word.
 * Color never stands alone: the label is always there.
 */
export function StateBadge({ state, className }: { state: string | null | undefined; className?: string }) {
  const meta = stateMeta(state);
  return (
    <span
      title={meta.meaning}
      // Baker Street draws this as a rubber stamp. The tone is its signal color.
      data-state-badge={state === "block" ? "red" : state === "review" ? "yellow" : "gray"}
      className={cn(
        "inline-flex h-[22px] items-center rounded-[5px] px-2 text-[12px] font-medium whitespace-nowrap",
        meta.badge,
        className,
      )}
    >
      {meta.label}
    </span>
  );
}

/** Marks the account of the person who uses the agent chat. */
export function YouTag() {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span className="inline-flex h-[18px] items-center rounded-sm border border-line-strong px-1.5 text-[10.5px] font-medium text-ink-2">
          You
        </span>
      </TooltipTrigger>
      <TooltipContent>This is your agent chat account.</TooltipContent>
    </Tooltip>
  );
}
