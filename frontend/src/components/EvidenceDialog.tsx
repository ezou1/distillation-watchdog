import { useEffect, useRef } from "react";

import type { CanonicalMessage, EvidenceDetail } from "@/api";
import { Problem, Quiet } from "@/components/Notes";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { useEvidence } from "@/hooks/queries";
import { formatNumber, formatSimDateTime } from "@/lib/format";
import { detectorMeta } from "@/lib/states";
import { cn } from "@/lib/utils";

const ROLE_LABEL: Record<string, string> = {
  user: "User",
  assistant: "Assistant",
  tool_call: "Tool call",
  tool_result: "Tool result",
};

function Message({ message, index, cited }: { message: CanonicalMessage; index: number; cited: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (cited) ref.current?.scrollIntoView({ block: "center" });
  }, [cited]);

  const role = ROLE_LABEL[message.role] ?? message.role;
  return (
    <div
      ref={ref}
      className={cn(
        "rounded-md border border-line bg-background",
        cited && "border-brand bg-brand-wash ring-1 ring-brand",
      )}
    >
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 border-b border-line px-3 py-1.5">
        <span className="font-mono text-[11px] text-ink-4 tabular-nums">#{index}</span>
        <span className="text-[12px] font-medium">{role}</span>
        {message.tool && <span className="font-mono text-[11px] text-ink-3">{message.tool}</span>}
        {cited && (
          <span className="ml-auto rounded-full bg-brand px-2 py-px text-[11px] font-medium text-white">
            The evidence points here
          </span>
        )}
      </div>
      {message.text && (
        <pre className="max-h-72 overflow-auto px-3 py-2 font-mono text-[11.5px] leading-relaxed whitespace-pre-wrap text-ink-2">
          {message.text}
        </pre>
      )}
      {message.args && (
        <pre className="max-h-48 overflow-auto border-t border-line px-3 py-2 font-mono text-[11.5px] leading-relaxed whitespace-pre-wrap text-ink-3">
          {JSON.stringify(message.args, null, 2)}
        </pre>
      )}
      {!message.text && !message.args && <p className="px-3 py-2 text-[12px] text-ink-4">No text.</p>}
    </div>
  );
}

function Body({ detail }: { detail: EvidenceDetail }) {
  const request = detail.request ?? {};
  const messages = request.messages ?? [];
  const citedIndex = detail.message?.index ?? detail.evidence.message_index ?? null;
  const params: Array<[string, string]> = [
    ["Account", request.pseudonym ?? "–"],
    ["Request", request.request_id ?? "–"],
    ["Time (sim)", formatSimDateTime(request.sim_time) || "–"],
    ["Temperature", formatNumber(request.temperature)],
    ["n", formatNumber(request.n)],
    ["Max tokens", formatNumber(request.max_tokens)],
    ["Tools schema", request.tools_schema_id ?? "–"],
  ];

  return (
    <>
      <dl className="grid grid-cols-2 gap-x-6 gap-y-2 border-b border-line px-5 py-3 sm:grid-cols-4">
        {params.map(([label, value]) => (
          <div key={label} className="min-w-0">
            <dt className="text-[11px] text-ink-3">{label}</dt>
            <dd className="truncate font-mono text-[12px]" title={value}>
              {value}
            </dd>
          </div>
        ))}
      </dl>
      <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
        {messages.length === 0 ? (
          <Quiet className="px-0">The watchdog did not keep the request of this evidence item.</Quiet>
        ) : (
          <>
            <p className="mb-3 text-[12px] text-ink-3">
              The request has {messages.length} {messages.length === 1 ? "message" : "messages"}.
              {citedIndex !== null && ` The evidence points to message #${citedIndex}.`}
            </p>
            <div className="grid gap-2">
              {messages.map((message, position) => {
                const index = message.index ?? position;
                return <Message key={index} message={message} index={index} cited={index === citedIndex} />;
              })}
            </div>
          </>
        )}
      </div>
    </>
  );
}

/** The exact request behind one evidence item, with the cited message marked. */
export function EvidenceDialog({ id, onClose }: { id: string | null; onClose: () => void }) {
  const query = useEvidence(id);
  const detector = detectorMeta(query.data?.evidence.detector);

  return (
    <Dialog open={id !== null} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="flex max-h-[88dvh] flex-col gap-0 overflow-hidden border-line-strong bg-panel p-0 sm:max-w-3xl">
        <DialogHeader className="gap-1 border-b border-line px-5 py-4 text-left">
          <DialogTitle className="flex flex-wrap items-baseline gap-x-3 text-[14px]">
            <span>
              {query.data ? `${detector.code} ${detector.name}` : "Evidence"}
            </span>
            <span className="font-mono text-[11.5px] font-normal text-ink-3">{id}</span>
          </DialogTitle>
          <DialogDescription className="max-w-[72ch] text-[12.5px] text-ink-2">
            {query.data?.evidence.summary ?? "The exact request behind this evidence item."}
          </DialogDescription>
        </DialogHeader>
        {query.isPending && <Quiet>Loading the request.</Quiet>}
        {query.isError && (
          <div className="p-5">
            <Problem>The watchdog cannot give this evidence item. {query.error.message}</Problem>
          </div>
        )}
        {query.data && <Body detail={query.data} />}
      </DialogContent>
    </Dialog>
  );
}
