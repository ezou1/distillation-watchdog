import type { Contribution } from "@/api";
import { formatSigned } from "@/lib/format";
import { detectorMeta } from "@/lib/states";

const MAX_ROWS = 10;

/**
 * How much each feature pushes the score up or down (log-odds), largest first.
 * Bars grow from a center line: right raises the score, left lowers it. The signed number repeats the direction,
 * so the meaning does not depend on color.
 */
export function ContributionBars({ contributions }: { contributions: Contribution[] }) {
  const rows = [...contributions].sort((a, b) => Math.abs(b.value) - Math.abs(a.value)).slice(0, MAX_ROWS);
  const scale = Math.max(...rows.map((row) => Math.abs(row.value)), 0.0001);

  return (
    <div>
      <ul className="mb-3 flex flex-wrap gap-x-4 gap-y-1 text-[12px] text-ink-2">
        <li className="flex items-center gap-1.5">
          <span className="size-2 rounded-[2px] bg-(--viz-lower)" aria-hidden="true" />
          Lowers the score
        </li>
        <li className="flex items-center gap-1.5">
          <span className="size-2 rounded-[2px] bg-(--viz-raise)" aria-hidden="true" />
          Raises the score
        </li>
      </ul>
      <ol className="grid gap-1.5">
        {rows.map((row) => {
          const share = (Math.abs(row.value) / scale) * 50;
          const raises = row.value >= 0;
          const detector = detectorMeta(row.name);
          return (
            <li
              key={row.name}
              title={detector.signal ? `${detector.code} ${detector.name}: ${detector.signal}` : row.name}
              className="grid grid-cols-[minmax(0,14rem)_minmax(4rem,1fr)_3.25rem] items-center gap-3"
            >
              <span className="truncate font-mono text-[11.5px] text-ink-2">{row.name}</span>
              <span className="relative h-3.5">
                <span className="absolute inset-y-0 left-1/2 w-px bg-(--viz-axis)" aria-hidden="true" />
                <span
                  className={
                    raises
                      ? "absolute inset-y-0.5 left-1/2 rounded-r-[3px] bg-(--viz-raise)"
                      : "absolute inset-y-0.5 right-1/2 rounded-l-[3px] bg-(--viz-lower)"
                  }
                  style={{ width: `${Math.max(share, 0.75)}%` }}
                />
              </span>
              <span className="text-right font-mono text-[11.5px] tabular-nums">{formatSigned(row.value)}</span>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
