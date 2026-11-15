import { formatScore } from "@/lib/format";
import { cn } from "@/lib/utils";

function clamp01(value: number): number {
  return Math.max(0, Math.min(1, value));
}

/** The risk bar: the share of the track that is filled is the score (0 to 1). */
export function RiskBar({ score, className }: { score: number | null | undefined; className?: string }) {
  const known = typeof score === "number" && Number.isFinite(score);
  return (
    <div
      className={cn("h-1.5 overflow-hidden rounded-full bg-hover", className)}
      role="meter"
      aria-label="Risk score"
      aria-valuemin={0}
      aria-valuemax={1}
      aria-valuenow={known ? clamp01(score) : undefined}
      aria-valuetext={known ? formatScore(score) : "no score"}
    >
      {known && <div className="risk-fill h-full rounded-full" style={{ width: `${clamp01(score) * 100}%` }} />}
    </div>
  );
}

/** Score for a table cell: the number, then a short risk bar. */
export function ScoreCell({ score }: { score: number | null | undefined }) {
  return (
    <span className="inline-flex items-center gap-2">
      <span className="w-8 text-right font-mono text-[12.5px] font-medium tabular-nums">{formatScore(score)}</span>
      <RiskBar score={score} className="h-1 w-16" />
    </span>
  );
}
