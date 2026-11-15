import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { watchdog, type CampaignEvent, type FlagEvent, type Summary } from "@/api";

export type FeedItem =
  | { key: number; kind: "flag" | "block"; simTime: number | null; event: FlagEvent }
  | { key: number; kind: "campaign"; simTime: number | null; event: CampaignEvent };

export interface StreamState {
  summary: Summary | null;
  /** null until the first answer from the watchdog. */
  online: boolean | null;
  /** Newest first. */
  feed: FeedItem[];
}

const FEED_MAX = 60;
const QUIET_AFTER_MS = 3000;
const RECONNECT_MS = 2000;
const POLL_MS = 2000;

// "?nostream" turns the event stream off; the page then polls the summary. Headless screenshots use it.
const NO_STREAM = new URLSearchParams(window.location.search).has("nostream");

function parse<T>(data: string): T | null {
  try {
    return JSON.parse(data) as T;
  } catch {
    return null;
  }
}

/**
 * The one connection to GET /api/stream. It gives the summary each second and the flag, block and
 * campaign events. When the stream is quiet it polls GET /api/summary, which also detects "offline".
 */
export function useStream(enabled: boolean): StreamState {
  const [summary, setSummary] = useState<Summary | null>(null);
  const [online, setOnline] = useState<boolean | null>(null);
  const [feed, setFeed] = useState<FeedItem[]>([]);
  const [quiet, setQuiet] = useState(NO_STREAM);
  const lastEventAt = useRef(0);
  const simTime = useRef<number | null>(null);
  const nextKey = useRef(0);

  useEffect(() => {
    if (!enabled || NO_STREAM || typeof EventSource === "undefined") {
      setQuiet(true);
      return;
    }
    let source: EventSource | null = null;
    let reconnect: ReturnType<typeof setTimeout> | undefined;
    let stopped = false;

    const push = (item: Omit<FeedItem, "key" | "simTime">) => {
      const full = { ...item, key: nextKey.current++, simTime: simTime.current } as FeedItem;
      setFeed((items) => [full, ...items].slice(0, FEED_MAX));
    };

    const connect = () => {
      if (stopped) return;
      source = new EventSource(watchdog.streamUrl);
      source.addEventListener("summary", (message) => {
        const data = parse<Summary>((message as MessageEvent<string>).data);
        if (!data) return;
        lastEventAt.current = Date.now();
        simTime.current = data.sim_time;
        setSummary(data);
        setOnline(true);
        setQuiet(false);
      });
      for (const kind of ["flag", "block"] as const) {
        source.addEventListener(kind, (message) => {
          const event = parse<FlagEvent>((message as MessageEvent<string>).data);
          if (event) push({ kind, event });
        });
      }
      source.addEventListener("campaign", (message) => {
        const event = parse<CampaignEvent>((message as MessageEvent<string>).data);
        if (event) push({ kind: "campaign", event });
      });
      source.onerror = () => {
        // The browser retries by itself unless the stream is closed.
        if (source?.readyState === EventSource.CLOSED) reconnect = setTimeout(connect, RECONNECT_MS);
      };
    };

    connect();
    const watch = setInterval(() => setQuiet(Date.now() - lastEventAt.current > QUIET_AFTER_MS), 1000);
    return () => {
      stopped = true;
      clearInterval(watch);
      clearTimeout(reconnect);
      source?.close();
    };
  }, [enabled]);

  const poll = useQuery({
    queryKey: ["summary"],
    queryFn: watchdog.summary,
    enabled: enabled && quiet,
    refetchInterval: POLL_MS,
  });

  useEffect(() => {
    if (!poll.data) return;
    simTime.current = poll.data.sim_time;
    setSummary(poll.data);
    setOnline(true);
  }, [poll.data, poll.dataUpdatedAt]);

  useEffect(() => {
    if (quiet && poll.isError) setOnline(false);
  }, [quiet, poll.isError, poll.errorUpdatedAt]);

  return { summary, online, feed };
}
