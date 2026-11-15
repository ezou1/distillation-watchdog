import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ArrowUpRight, ChevronRight, LoaderCircle, Plus, SendHorizontal, SquareTerminal, Wrench } from "lucide-react";
import { useEffect, useRef, useState, type FormEvent } from "react";

import { agent, type AgentMessage, type AgentSession, type AgentToolCall } from "@/api";
import { HelpButton, HelpNote, Problem, Quiet, useHelp } from "@/components/Notes";
import { PageHeader } from "@/components/PageHeader";
import { SectionLabel } from "@/components/Panel";
import { Button } from "@/components/ui/button";
import { useAgentFiles, useAgentSession } from "@/hooks/queries";
import { useApp } from "@/lib/app-context";
import { formatBytes } from "@/lib/format";
import { agentSite } from "@/lib/origin";
import { cn } from "@/lib/utils";

const SESSION_KEY = ["agent", "session"] as const;
const FILES_KEY = ["agent", "files"] as const;

const STATUS_TEXT: Record<string, string> = {
  awaiting_approval: "The agent waits for your decision on the shell command.",
  step_limit: "The agent stopped at the step limit. Send a message to continue.",
  error: "The agent stopped with an error.",
};

/** The one argument that says most about a tool call: the command, or the path. */
function callSummary(call: AgentToolCall): string {
  const args = call.arguments ?? {};
  const value = args.command ?? args.path ?? args.pattern ?? args.query;
  return typeof value === "string" ? value : "";
}

function ToolCard({ call, result }: { call: AgentToolCall; result: AgentMessage | undefined }) {
  const shell = call.name === "run_shell";
  const Icon = shell ? SquareTerminal : Wrench;
  const input = shell && typeof call.arguments?.command === "string" ? call.arguments.command : JSON.stringify(call.arguments, null, 2);
  return (
    <details className="group rounded-md border border-line bg-panel">
      <summary className="flex cursor-pointer list-none items-center gap-2 px-3 py-1.5 select-none [&::-webkit-details-marker]:hidden">
        <ChevronRight className="size-3.5 shrink-0 text-ink-4 transition-transform group-open:rotate-90" aria-hidden="true" />
        <Icon className="size-3.5 shrink-0 text-ink-3" aria-hidden="true" />
        <span className="font-mono text-[12px]">{call.name}</span>
        <span className="min-w-0 flex-1 truncate font-mono text-[11.5px] text-ink-3">{callSummary(call)}</span>
        {!result && <span className="shrink-0 text-[11.5px] text-ink-3">waiting</span>}
      </summary>
      <div className="grid gap-2 border-t border-line p-3">
        <div>
          <SectionLabel>Input</SectionLabel>
          <pre className="mt-1 max-h-64 overflow-auto font-mono text-[11.5px] leading-relaxed whitespace-pre-wrap text-ink-2">
            {input}
          </pre>
        </div>
        {result && (
          <div>
            <SectionLabel>Output</SectionLabel>
            <pre className="mt-1 max-h-64 overflow-auto font-mono text-[11.5px] leading-relaxed whitespace-pre-wrap text-ink-2">
              {result.content || "(no output)"}
            </pre>
          </div>
        )}
      </div>
    </details>
  );
}

function Turn({ who, children }: { who: "You" | "Agent"; children: React.ReactNode }) {
  return (
    <div className="grid gap-1.5">
      <div className="micro">{who}</div>
      {children}
    </div>
  );
}

function Transcript({ session, optimistic }: { session: AgentSession | undefined; optimistic: string | null }) {
  const messages = session?.messages ?? [];
  const results = new Map<string, AgentMessage>();
  for (const message of messages) {
    if (message.role === "tool" && message.tool_call_id) results.set(message.tool_call_id, message);
  }
  return (
    <div className="grid gap-5">
      {messages.map((message, index) => {
        if (message.role === "user") {
          return (
            <Turn key={index} who="You">
              <p className="text-[13.5px] leading-relaxed whitespace-pre-wrap">{message.content}</p>
            </Turn>
          );
        }
        if (message.role === "assistant") {
          return (
            <Turn key={index} who="Agent">
              {message.content && (
                <p className="text-[13.5px] leading-relaxed whitespace-pre-wrap text-ink-2">{message.content}</p>
              )}
              {message.tool_calls?.map((call) => <ToolCard key={call.id} call={call} result={results.get(call.id)} />)}
            </Turn>
          );
        }
        return null;
      })}
      {optimistic && (
        <Turn who="You">
          <p className="text-[13.5px] leading-relaxed whitespace-pre-wrap">{optimistic}</p>
        </Turn>
      )}
    </div>
  );
}

function ApprovalCard({
  command,
  busy,
  onDecide,
}: {
  command: string;
  busy: boolean;
  onDecide: (approve: boolean) => void;
}) {
  return (
    <div className="rounded-lg border border-st-review/50 bg-st-review/10 p-4" role="group" aria-label="Shell command approval">
      <h3 className="text-[13px] font-semibold">The agent wants to run this shell command</h3>
      <p className="mt-1 max-w-[70ch] text-[12.5px] text-ink-2">
        Read the command before you approve it. It runs in the sandbox folder, but a shell command can also reach files
        outside that folder.
      </p>
      <pre className="mt-3 overflow-x-auto rounded-md border border-line bg-background px-3 py-2 font-mono text-[12.5px] whitespace-pre-wrap">
        {command}
      </pre>
      <div className="mt-3 flex flex-wrap gap-2">
        <Button size="sm" className="rounded-full" disabled={busy} onClick={() => onDecide(true)}>
          Approve and run
        </Button>
        <Button
          size="sm"
          variant="outline"
          className="rounded-full border-line-strong bg-transparent"
          disabled={busy}
          onClick={() => onDecide(false)}
        >
          Reject
        </Button>
      </div>
    </div>
  );
}

/** Shown on the dashboard server, where the agent API is not available. */
function OtherServer() {
  return (
    <>
      <PageHeader title="Agent chat" />
      <div className="grid max-w-[70ch] gap-3 p-5">
        <p className="text-[13px] leading-relaxed text-ink-2">
          The agent chat runs on its own server, and it accepts requests only from its own page. Open it there to talk
          to the agent. Your account then shows on the Live page with the mark "You".
        </p>
        <div>
          <Button asChild size="sm" className="rounded-full">
            <a href={`${agentSite()}#/agent`}>
              Open the agent chat
              <ArrowUpRight />
            </a>
          </Button>
        </div>
      </div>
    </>
  );
}

export function AgentChat() {
  const { role } = useApp();
  if (role === "watchdog") return <OtherServer />;
  return <Chat />;
}

function Chat() {
  const client = useQueryClient();
  const [helpOpen, toggleHelp] = useHelp("agent");
  const [tools, setTools] = useState(true);
  const [draft, setDraft] = useState("");
  const [optimistic, setOptimistic] = useState<string | null>(null);
  const bottom = useRef<HTMLDivElement>(null);

  const session = useAgentSession(true);
  const files = useAgentFiles(true);

  const settle = {
    onSuccess: (data: AgentSession) => {
      if (Array.isArray(data.messages)) client.setQueryData(SESSION_KEY, data);
    },
    onSettled: () => {
      setOptimistic(null);
      void client.invalidateQueries({ queryKey: FILES_KEY });
    },
  };
  const send = useMutation({ mutationFn: (message: string) => agent.chat(message, tools), ...settle });
  const decide = useMutation({
    mutationFn: (choice: { id: string; approve: boolean }) => agent.approve(choice.id, choice.approve),
    ...settle,
  });
  const restart = useMutation({
    mutationFn: agent.newSession,
    onSuccess: () => client.invalidateQueries({ queryKey: SESSION_KEY }),
  });

  const busy = send.isPending || decide.isPending || restart.isPending;
  const data = session.data;
  const pending = data?.pending ?? null;
  const failure = send.error ?? decide.error ?? restart.error;

  useEffect(() => {
    bottom.current?.scrollIntoView({ block: "end" });
  }, [data?.messages.length, optimistic, pending]);

  const submit = (event?: FormEvent) => {
    event?.preventDefault();
    const text = draft.trim();
    if (!text || busy || pending) return;
    setDraft("");
    setOptimistic(text);
    send.mutate(text);
  };

  return (
    <div className="flex h-full flex-col">
      <PageHeader
        title="Agent chat"
        actions={
          <>
            <Button
              variant="outline"
              size="sm"
              aria-pressed={tools}
              onClick={() => setTools((on) => !on)}
              title="With tools the agent can read, write and edit files in the sandbox. Without tools it only answers."
              className={cn("rounded-full border-line-strong bg-transparent", !tools && "text-ink-3")}
            >
              <Wrench />
              Tools {tools ? "on" : "off"}
            </Button>
            <Button
              variant="outline"
              size="sm"
              className="rounded-full border-line-strong bg-transparent"
              disabled={busy}
              onClick={() => restart.mutate()}
            >
              <Plus />
              New session
            </Button>
            <HelpButton open={helpOpen} onToggle={toggleHelp} />
          </>
        }
      />

      <div className="grid min-h-0 flex-1 lg:grid-cols-[minmax(0,1fr)_17rem]">
        <div className="flex min-h-0 flex-col">
          <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4 sm:px-5">
            <div className="mx-auto grid max-w-3xl gap-5">
              <HelpNote open={helpOpen} onClose={toggleHelp}>
                Type a task and send it. The agent uses the lab model through the gateway, as a normal customer. With
                tools on, it reads, writes and edits files in the sandbox folder. Before it runs a shell command it
                stops and asks you. A new session starts a new conversation; the sandbox files stay.
              </HelpNote>

              {session.isError && !data && (
                <Problem>
                  The agent chat server does not answer. {session.error.message} Start it with{" "}
                  <code className="font-mono">scripts/demo.sh</code>.
                </Problem>
              )}
              {data && data.messages.length === 0 && !optimistic && (
                <Quiet className="px-0 py-2">
                  No messages yet. Ask the agent for a small task, for example: write a function add(a, b) and a test
                  for it.
                </Quiet>
              )}

              <Transcript session={data} optimistic={optimistic} />

              {busy && (
                <p className="flex items-center gap-2 text-[12.5px] text-ink-3" role="status">
                  <LoaderCircle className="size-3.5 animate-spin" aria-hidden="true" />
                  The agent works.
                </p>
              )}
              {pending && (
                <ApprovalCard
                  command={pending.command}
                  busy={busy}
                  onDecide={(approve) => decide.mutate({ id: pending.approval_id, approve })}
                />
              )}
              {!busy && data && STATUS_TEXT[data.status] && !pending && (
                <p className="text-[12.5px] text-ink-3">{STATUS_TEXT[data.status]}</p>
              )}
              {data?.error && <Problem>{data.error}</Problem>}
              {failure && !busy && <Problem>The request failed. {failure.message}</Problem>}
              <div ref={bottom} />
            </div>
          </div>

          <form onSubmit={submit} className="border-t border-line px-4 py-3 sm:px-5">
            <div className="mx-auto flex max-w-3xl items-end gap-2">
              <label htmlFor="agent-message" className="sr-only">
                Message to the agent
              </label>
              <textarea
                id="agent-message"
                value={draft}
                onChange={(event) => setDraft(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) submit();
                }}
                rows={2}
                placeholder={pending ? "Decide on the shell command first." : "Type a task for the agent. Ctrl+Enter sends it."}
                disabled={Boolean(pending)}
                className="min-h-[52px] flex-1 resize-y rounded-md border border-line-strong bg-panel px-3 py-2 text-[13.5px] leading-relaxed placeholder:text-ink-4 focus-visible:outline-2 focus-visible:outline-brand disabled:opacity-60"
              />
              <Button type="submit" size="sm" className="h-[52px] rounded-md px-4" disabled={busy || Boolean(pending) || !draft.trim()}>
                <SendHorizontal />
                Send
              </Button>
            </div>
          </form>
        </div>

        <aside className="hidden min-h-0 overflow-y-auto border-l border-line p-4 lg:block" aria-label="Sandbox files">
          <SectionLabel>Sandbox files</SectionLabel>
          {files.isError ? (
            <p className="mt-2 text-[12px] text-ink-3">The file list is not available.</p>
          ) : (files.data?.length ?? 0) === 0 ? (
            <p className="mt-2 text-[12px] text-ink-3">No files yet. The agent writes them here.</p>
          ) : (
            <ul className="mt-2 grid gap-px">
              {files.data?.map((file) => (
                <li key={file.path} className="flex items-baseline justify-between gap-3 py-1">
                  <span className="min-w-0 truncate font-mono text-[12px]" title={file.path}>
                    {file.path}
                  </span>
                  <span className="shrink-0 font-mono text-[11px] text-ink-4 tabular-nums">{formatBytes(file.size)}</span>
                </li>
              ))}
            </ul>
          )}
        </aside>
      </div>
    </div>
  );
}
