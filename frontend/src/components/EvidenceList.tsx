import type { Evidence } from "@/api";
import { useApp } from "@/lib/app-context";
import { formatSimTime } from "@/lib/format";
import { DETECTOR_ORDER, detectorKey, detectorMeta } from "@/lib/states";
import { cn } from "@/lib/utils";

interface RowProps {
  item: Evidence;
  /** Highlighted because the matching citation in the case file is under the pointer. */
  hot?: boolean;
  onHot?: (id: string | null) => void;
  showDetector?: boolean;
}

function EvidenceRow({ item, hot, onHot, showDetector }: RowProps) {
  const { openEvidence } = useApp();
  const detector = detectorMeta(item.detector);
  const time = formatSimTime(item.sim_time);
  return (
    <li>
      <button
        type="button"
        onClick={() => openEvidence(item.id)}
        onMouseEnter={onHot && (() => onHot(item.id))}
        onMouseLeave={onHot && (() => onHot(null))}
        onFocus={onHot && (() => onHot(item.id))}
        onBlur={onHot && (() => onHot(null))}
        data-evidence={item.id}
        className={cn(
          "grid w-full grid-cols-[auto_minmax(0,1fr)_auto] items-baseline gap-x-3 rounded-md px-2 py-1.5 text-left transition-colors hover:bg-hover",
          hot && "bg-brand-wash",
        )}
      >
        <span className="font-mono text-[11px] text-ink-3">{item.id}</span>
        <span className="text-[12.5px] leading-snug text-ink-2">
          {showDetector && item.detector && <span className="micro mr-2">{detector.code}</span>}
          {item.summary || "No summary."}
        </span>
        <span className="font-mono text-[11px] text-ink-4 tabular-nums">{time}</span>
      </button>
    </li>
  );
}

/** A flat list of evidence items. Each row opens the exact request behind it. */
export function EvidenceList({
  evidence,
  hot,
  onHot,
}: {
  evidence: Evidence[];
  hot?: string | null;
  onHot?: (id: string | null) => void;
}) {
  return (
    <ul className="grid gap-px">
      {evidence.map((item) => (
        <EvidenceRow key={item.id} item={item} hot={hot === item.id} onHot={onHot} showDetector />
      ))}
    </ul>
  );
}

/** Evidence grouped by detector, with the signal of each detector in plain words. */
export function EvidenceByDetector({ evidence, limitPerGroup }: { evidence: Evidence[]; limitPerGroup?: number }) {
  const groups = new Map<string, Evidence[]>();
  for (const item of evidence) {
    const key = detectorKey(item.detector);
    groups.set(key, [...(groups.get(key) ?? []), item]);
  }
  const ordered = DETECTOR_ORDER.filter((key) => groups.has(key));

  return (
    <div className="grid gap-5">
      {ordered.map((key) => {
        const items = groups.get(key) ?? [];
        const shown = limitPerGroup ? items.slice(0, limitPerGroup) : items;
        const meta = detectorMeta(key);
        return (
          <section key={key}>
            <header className="mb-1 flex flex-wrap items-baseline gap-x-3 gap-y-0.5 px-2">
              <h4 className="micro text-ink-2">
                {meta.code} {meta.name}
              </h4>
              <span className="text-[12px] text-ink-3">{meta.signal}</span>
              <span className="ml-auto font-mono text-[11px] text-ink-4 tabular-nums">{items.length}</span>
            </header>
            <ul className="grid gap-px">
              {shown.map((item) => (
                <EvidenceRow key={item.id} item={item} />
              ))}
            </ul>
            {shown.length < items.length && (
              <p className="px-2 pt-1 text-[12px] text-ink-4">
                {items.length - shown.length} more on the full account page.
              </p>
            )}
          </section>
        );
      })}
    </div>
  );
}
