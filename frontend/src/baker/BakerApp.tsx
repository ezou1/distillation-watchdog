import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";

import "@/baker/baker.css";

import { Board } from "@/baker/Board";
import { AccountsDisplay, DossierDisplay } from "@/baker/Accounts";
import { AgentGraphDisplay } from "@/baker/AgentGraph";
import { ActivityDisplay, EvaluationDisplay, MetricsDisplay } from "@/baker/Displays";
import { MessagesDisplay } from "@/baker/Messages";
import { ZoomContext } from "@/baker/parts";
import { Search } from "@/baker/Search";
import { useApp } from "@/lib/app-context";
import { navigate, type Route } from "@/lib/router";

const ZOOM_MS = 440;
const EASING = "cubic-bezier(0.3, 0, 0.15, 1)";

/** The postcard (or the folder) that a route zooms out of. Null for the board. */
function cardOf(route: Route): string | null {
  switch (route.page) {
    case "activity":
    case "messages":
    case "metrics":
      return route.page;
    case "accounts":
    case "account":
      return "accounts";
    case "campaigns":
      return "graph";
    case "eval":
      return "eval";
    default:
      return null;
  }
}

/** Where Back and Esc go: an account dossier goes to the accounts list, all other displays go to the board. */
function parentOf(route: Route): Route {
  return route.page === "account" ? { page: "accounts" } : { page: "live" };
}

function Display({ route }: { route: Route }) {
  switch (route.page) {
    case "activity":
      return <ActivityDisplay />;
    case "accounts":
      return <AccountsDisplay />;
    case "account":
      return <DossierDisplay id={route.id} />;
    case "messages":
      return <MessagesDisplay />;
    case "metrics":
      return <MetricsDisplay />;
    case "campaigns":
      return <AgentGraphDisplay selected={route.id ?? null} />;
    case "eval":
      return <EvaluationDisplay />;
    default:
      return null;
  }
}

function reducedMotion(): boolean {
  return window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
}

/** The steps of the zoom: the display grows out of its postcard. Only transform and opacity change. */
function zoomFrames(card: string | null): Keyframe[] {
  const element = card ? document.querySelector(`[data-bk-card="${card}"]`) : null;
  if (!element || reducedMotion()) return [{ opacity: 0 }, { opacity: 1 }];
  const box = element.getBoundingClientRect();
  const from = `translate(${box.left}px, ${box.top}px) scale(${box.width / window.innerWidth}, ${box.height / window.innerHeight})`;
  return [
    { transform: from, opacity: 0 },
    { opacity: 1, offset: 0.3 },
    { transform: "none", opacity: 1 },
  ];
}

/** Baker Street: the board, and one display that zooms in over it. */
export function BakerApp({ route }: { route: Route }) {
  const { stream } = useApp();
  const [searchOpen, setSearchOpen] = useState(false);
  const [zooming, setZooming] = useState(false);

  const card = cardOf(route);
  // The display stays on the page while it zooms out, so the route it shows lags behind the real route.
  const [shown, setShown] = useState<Route | null>(card ? route : null);
  const shownRef = useRef(shown);
  const layer = useRef<HTMLDivElement | null>(null);
  const zoomIn = useRef<string | null>(null);
  const lastCard = useRef<string | null>(card);

  // The theme lives on <html>, so dialogs (which React puts outside the app root) get it too.
  useLayoutEffect(() => {
    const root = document.documentElement;
    const title = document.title;
    root.classList.add("baker");
    document.title = "Baker Street";
    return () => {
      root.classList.remove("baker");
      document.title = title;
    };
  }, []);

  useLayoutEffect(() => {
    if (card) {
      if (!shownRef.current) zoomIn.current = card;
      lastCard.current = card;
      shownRef.current = route;
      setShown(route);
      return;
    }
    if (!shownRef.current) return;
    const done = () => {
      shownRef.current = null;
      setShown(null);
      // The postcard is a box; the link inside it takes the focus. The folder is a link itself.
      const origin = document.querySelector<HTMLElement>(`[data-bk-card="${lastCard.current}"]`);
      (origin?.querySelector<HTMLElement>("a") ?? origin)?.focus({ preventScroll: true });
    };
    const element = layer.current;
    if (!element) return done();
    const animation = element.animate(zoomFrames(lastCard.current).reverse(), { duration: ZOOM_MS, easing: EASING, fill: "forwards" });
    animation.onfinish = done;
    // A new route during the zoom stops it. The next run of this effect decides what to show.
    return () => animation.cancel();
  }, [route, card]);

  // The zoom in starts after React has put the display on the page.
  useLayoutEffect(() => {
    if (!zoomIn.current || !layer.current) return;
    const animation = layer.current.animate(zoomFrames(zoomIn.current), { duration: ZOOM_MS, easing: EASING });
    zoomIn.current = null;
    setZooming(true);
    animation.onfinish = animation.oncancel = () => setZooming(false);
    // Focus goes to the display, not to the Back button: Enter must stay free for the lists.
    layer.current.focus({ preventScroll: true });
  }, [shown]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setSearchOpen((open) => !open);
        return;
      }
      // A dialog handles Esc by itself.
      if (event.key !== "Escape" || !card || document.querySelector('[role="dialog"]')) return;
      event.preventDefault();
      navigate(parentOf(route));
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [route, card]);

  const openSearch = useCallback(() => setSearchOpen(true), []);
  return (
    <div className="bk-root">
      <Board stream={stream} away={Boolean(card)} onSearch={openSearch} />
      {shown && (
        <div className="bk-layer" ref={layer} tabIndex={-1}>
          <ZoomContext.Provider value={zooming}>
            <Display route={card ? route : shown} />
          </ZoomContext.Provider>
        </div>
      )}
      <Search open={searchOpen} onOpenChange={setSearchOpen} />
    </div>
  );
}
