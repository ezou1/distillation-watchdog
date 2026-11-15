import { formatSigned } from "@/lib/format";
import { detectorMeta, parseReason } from "@/lib/states";

/** The top reasons of an account row: feature name and its contribution to the score. */
export function ReasonChips({ reasons, max = 3 }: { reasons: string[] | undefined; max?: number }) {
  const items = (reasons ?? []).slice(0, max).map(parseReason);
  if (items.length === 0) return <span className="text-ink-4">–</span>;
  return (
    <span className="flex flex-wrap gap-1">
      {items.map(({ feature, value }) => {
        const detector = detectorMeta(feature);
        return (
          <span
            key={feature}
            title={detector.signal ? `${detector.code} ${detector.name}: ${detector.signal}` : undefined}
            className="inline-flex h-5 items-center gap-1.5 rounded-sm bg-hover px-1.5 font-mono text-[11px] text-ink-2"
          >
            {feature}
            {value !== null && <span className="text-ink-3 tabular-nums">{formatSigned(value, 1)}</span>}
          </span>
        );
      })}
    </span>
  );
}
