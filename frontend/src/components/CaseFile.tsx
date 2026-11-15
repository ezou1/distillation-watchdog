import { FileText, LoaderCircle } from "lucide-react";
import { useState, type ReactNode } from "react";

import type { Casefile } from "@/api";
import { EvidenceList } from "@/components/EvidenceList";
import { Problem, Quiet } from "@/components/Notes";
import { SectionLabel } from "@/components/Panel";
import { Button } from "@/components/ui/button";
import { useCasefile } from "@/hooks/queries";
import { useApp } from "@/lib/app-context";
import { cn } from "@/lib/utils";

// ---------- a small Markdown renderer for the case-file text ----------
// The text comes from the watchdog, which accepts it only if each claim cites evidence.
// Supported: headings, paragraphs, lists, fenced code, `code`, **bold**, and [ev-...] citations.

type Block =
  | { kind: "heading"; level: number; text: string }
  | { kind: "paragraph"; text: string }
  | { kind: "list"; ordered: boolean; items: string[] }
  | { kind: "code"; text: string };

function parseBlocks(markdown: string): Block[] {
  const blocks: Block[] = [];
  let paragraph: string[] = [];
  let list: { ordered: boolean; items: string[] } | null = null;
  let code: string[] | null = null;

  const flushParagraph = () => {
    if (paragraph.length) blocks.push({ kind: "paragraph", text: paragraph.join(" ") });
    paragraph = [];
  };
  const flushList = () => {
    if (list) blocks.push({ kind: "list", ...list });
    list = null;
  };

  for (const line of markdown.split(/\r?\n/)) {
    if (code !== null) {
      if (/^\s*```/.test(line)) {
        blocks.push({ kind: "code", text: code.join("\n") });
        code = null;
      } else code.push(line);
      continue;
    }
    if (/^\s*```/.test(line)) {
      flushParagraph();
      flushList();
      code = [];
      continue;
    }
    const heading = /^(#{1,6})\s+(.*)$/.exec(line);
    if (heading) {
      flushParagraph();
      flushList();
      blocks.push({ kind: "heading", level: heading[1]?.length ?? 2, text: heading[2] ?? "" });
      continue;
    }
    const bullet = /^\s*[-*+]\s+(.*)$/.exec(line);
    const numbered = /^\s*\d+[.)]\s+(.*)$/.exec(line);
    const item = bullet ?? numbered;
    if (item) {
      flushParagraph();
      const ordered = !bullet;
      if (list && list.ordered !== ordered) flushList();
      list ??= { ordered, items: [] };
      list.items.push(item[1] ?? "");
      continue;
    }
    if (!line.trim()) {
      flushParagraph();
      flushList();
      continue;
    }
    flushList();
    paragraph.push(line.trim());
  }
  if (code !== null) blocks.push({ kind: "code", text: code.join("\n") });
  flushParagraph();
  flushList();
  return blocks;
}

const INLINE = /(`[^`]+`)|(\*\*[^*]+\*\*)|(\[ev-[\w-]+(?:\s*[,;]\s*ev-[\w-]+)*\])/g;

interface CiteProps {
  hot: string | null;
  onHot: (id: string | null) => void;
}

/** A citation chip. The dashed outline ties it to the evidence row below, which lights up with it. */
function Cite({ id, hot, onHot }: CiteProps & { id: string }) {
  const { openEvidence } = useApp();
  return (
    <button
      type="button"
      onClick={() => openEvidence(id)}
      onMouseEnter={() => onHot(id)}
      onMouseLeave={() => onHot(null)}
      onFocus={() => onHot(id)}
      onBlur={() => onHot(null)}
      className={cn(
        "mx-0.5 inline-flex items-center rounded-sm border border-dashed border-brand/70 px-1 align-baseline font-mono text-[11px] leading-[1.5] text-brand-ink transition-colors hover:bg-brand-wash",
        hot === id && "border-solid bg-brand-wash",
      )}
    >
      {id}
    </button>
  );
}

function Inline({ text, hot, onHot }: CiteProps & { text: string }): ReactNode {
  const out: ReactNode[] = [];
  let cursor = 0;
  for (const match of text.matchAll(INLINE)) {
    const start = match.index;
    if (start > cursor) out.push(text.slice(cursor, start));
    const token = match[0];
    if (match[1]) {
      out.push(
        <code key={start} className="rounded-sm bg-hover px-1 font-mono text-[0.92em]">
          {token.slice(1, -1)}
        </code>,
      );
    } else if (match[2]) {
      out.push(
        <strong key={start} className="font-semibold text-foreground">
          {token.slice(2, -2)}
        </strong>,
      );
    } else {
      const ids = token.slice(1, -1).split(/\s*[,;]\s*/);
      out.push(
        <span key={start} className="whitespace-nowrap">
          {ids.map((id) => (
            <Cite key={id} id={id} hot={hot} onHot={onHot} />
          ))}
        </span>,
      );
    }
    cursor = start + token.length;
  }
  if (cursor < text.length) out.push(text.slice(cursor));
  return out;
}

function Markdown({ text, hot, onHot }: CiteProps & { text: string }) {
  return (
    <div className="grid max-w-[76ch] gap-3 text-[13px] leading-relaxed text-ink-2">
      {parseBlocks(text).map((block, index) => {
        switch (block.kind) {
          case "heading":
            return (
              <h4 key={index} className="mt-2 text-[13px] font-semibold text-foreground first:mt-0">
                <Inline text={block.text} hot={hot} onHot={onHot} />
              </h4>
            );
          case "code":
            return (
              <pre key={index} className="overflow-x-auto rounded-md border border-line bg-background px-3 py-2 font-mono text-[11.5px]">
                {block.text}
              </pre>
            );
          case "list": {
            const Tag = block.ordered ? "ol" : "ul";
            return (
              <Tag key={index} className={cn("grid gap-1.5 pl-5", block.ordered ? "list-decimal" : "list-disc")}>
                {block.items.map((item, itemIndex) => (
                  <li key={itemIndex} className="marker:text-ink-4">
                    <Inline text={item} hot={hot} onHot={onHot} />
                  </li>
                ))}
              </Tag>
            );
          }
          default:
            return (
              <p key={index}>
                <Inline text={block.text} hot={hot} onHot={onHot} />
              </p>
            );
        }
      })}
    </div>
  );
}

// ---------- the case file ----------

function CaseFileResult({ casefile }: { casefile: Casefile }) {
  const [hot, setHot] = useState<string | null>(null);
  const evidence = casefile.evidence ?? [];
  const fallback = casefile.status === "fallback" || !casefile.text;

  return (
    <div className="grid gap-5">
      {fallback ? (
        <Problem>
          The case-file writer did not give a text in which each claim cites evidence, so only the evidence list is
          shown.{casefile.reason ? ` Reason: ${casefile.reason}` : ""}
        </Problem>
      ) : (
        <>
          <p className="text-[12px] text-ink-3">
            Each claim cites evidence. {casefile.cited?.length ?? 0} evidence{" "}
            {casefile.cited?.length === 1 ? "item is" : "items are"} cited
            {casefile.cached ? "; this text was written before and is unchanged." : "."}
          </p>
          <Markdown text={casefile.text} hot={hot} onHot={setHot} />
        </>
      )}
      <div>
        <SectionLabel className="mb-1 px-2">Evidence of this case file</SectionLabel>
        {evidence.length ? (
          <EvidenceList evidence={evidence} hot={hot} onHot={setHot} />
        ) : (
          <Quiet className="px-2 py-2">The watchdog has no evidence for this case yet.</Quiet>
        )}
      </div>
    </div>
  );
}

/** The button that asks the watchdog to write a case file. Put it in the header of the page. */
export function CaseFileButton({ kind, id }: { kind: "account" | "campaign"; id: string }) {
  const query = useCasefile(kind, id);
  return (
    <Button
      size="sm"
      className="rounded-full"
      disabled={query.isFetching}
      onClick={() => {
        void query.refetch();
      }}
    >
      {query.isFetching ? <LoaderCircle className="animate-spin" /> : <FileText />}
      {query.isFetching ? "Writing the case file" : query.data ? "Write the case file again" : "Generate case file"}
    </Button>
  );
}

/** The case file of an account or campaign. It shows nothing until the person asks for one. */
export function CaseFilePanel({ kind, id }: { kind: "account" | "campaign"; id: string }) {
  const query = useCasefile(kind, id);
  if (!query.data && !query.isFetching && !query.isError) return null;
  return (
    <section className="rounded-lg border border-line bg-panel">
      <header className="flex min-h-11 items-center gap-3 border-b border-line px-4 py-2">
        <h2 className="text-[13px] font-semibold tracking-[-0.01em]">Case file</h2>
        {query.isFetching && <span className="text-[12px] text-ink-3">The watchdog writes the text. This can take a minute.</span>}
      </header>
      <div className="p-4">
        {query.isError && !query.isFetching && (
          <Problem>The case file failed. {query.error.message} Try again.</Problem>
        )}
        {query.data && <CaseFileResult casefile={query.data} />}
        {!query.data && query.isFetching && <Quiet className="px-0 py-2">No text yet.</Quiet>}
      </div>
    </section>
  );
}
