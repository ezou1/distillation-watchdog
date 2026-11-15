import { useQuery } from "@tanstack/react-query";
import {
  Activity,
  ArrowUpRight,
  Bot,
  FlaskConical,
  Moon,
  PanelLeft,
  Search,
  Sun,
  Swords,
  Users,
  Waypoints,
  type LucideIcon,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { CommandPalette } from "@/components/CommandPalette";
import { EvidenceDialog } from "@/components/EvidenceDialog";
import { Problem } from "@/components/Notes";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { useStream, type StreamState } from "@/hooks/useStream";
import { AppContext, type AppContextValue } from "@/lib/app-context";
import { formatCount, formatNumber, formatSimDateTime } from "@/lib/format";
import {
  agentSite,
  detectRole,
  generatorSite,
  hasDashboard,
  isGenerator,
  watchdogSite,
  type GeneratorKind,
  type Role,
} from "@/lib/origin";
import { routeHref, useRoute, type Route } from "@/lib/router";
import { STATES, STATE_META } from "@/lib/states";
import { setTheme, useTheme } from "@/lib/theme";
import { cn } from "@/lib/utils";
import { Account } from "@/pages/Account";
import { AgentChat } from "@/pages/AgentChat";
import { Campaigns } from "@/pages/Campaigns";
import { Evaluation } from "@/pages/Evaluation";
import { Live } from "@/pages/Live";
import { TRAFFIC_TITLE, Traffic } from "@/pages/Traffic";

const SIDEBAR_KEY = "dw-sidebar-collapsed";

type NavId = "live" | "campaigns" | "eval" | "traffic-normal" | "traffic-attack" | "agent";

interface NavItem {
  id: NavId;
  label: string;
  icon: LucideIcon;
  href: string;
  /** The link leaves this server: the dashboard, the agent chat, and each generator are separate servers. */
  external: boolean;
}

/**
 * Every page in one sidebar. A page that another server serves is a link to that server's /app.
 * In dev the Vite server proxies all APIs, so every page is local.
 */
function navItems(role: Role): NavItem[] {
  const item = (id: NavId, label: string, icon: LucideIcon, route: Route, local: boolean, site: () => string): NavItem => ({
    id,
    label,
    icon,
    href: (local ? "" : site()) + routeHref(route),
    external: !local,
  });
  const dashboard = hasDashboard(role);
  const traffic = (kind: GeneratorKind) =>
    item(`traffic-${kind}`, TRAFFIC_TITLE[kind], kind === "attack" ? Swords : Users, { page: "traffic", kind },
      role === "dev" || role === kind, () => generatorSite(kind));
  return [
    item("live", "Live", Activity, { page: "live" }, dashboard, watchdogSite),
    item("campaigns", "Campaigns", Waypoints, { page: "campaigns" }, dashboard, watchdogSite),
    item("eval", "Evaluation", FlaskConical, { page: "eval" }, dashboard, watchdogSite),
    traffic("normal"),
    traffic("attack"),
    item("agent", "Agent chat", Bot, { page: "agent" }, role === "dev" || role === "agent", agentSite),
  ];
}

function isActive(item: NavItem, route: Route, role: Role): boolean {
  if (role === "agent") return item.id === "agent";
  if (isGenerator(role)) return item.id === `traffic-${role}`;
  if (item.id === "live") return route.page === "live" || route.page === "account";
  if (route.page === "traffic") return item.id === `traffic-${route.kind}`;
  return item.id === route.page;
}

function BrandMark() {
  return (
    <svg width="14" height="14" viewBox="0 0 18 18" aria-hidden="true" className="shrink-0">
      <circle cx="9" cy="9" r="7.25" fill="none" stroke="currentColor" strokeWidth="1.75" />
      <path d="M9 9 L9 1.75 A7.25 7.25 0 0 1 16.25 9 Z" fill="currentColor" />
      <circle cx="9" cy="9" r="1.75" fill="var(--brand)" stroke="currentColor" strokeWidth="1.5" />
    </svg>
  );
}

/** Linear-style groups. The traffic generators and the agent chat are the lab side of the demo. */
const NAV_GROUPS: Array<{ name: string; ids: NavId[] }> = [
  { name: "Watchdog", ids: ["live", "campaigns", "eval"] },
  { name: "Lab", ids: ["traffic-normal", "traffic-attack", "agent"] },
];

function Sidebar({
  items,
  route,
  role,
  collapsed,
  counts,
  onCollapse,
  onSearch,
}: {
  items: NavItem[];
  route: Route;
  role: Role;
  collapsed: boolean;
  /** A small count after a page name: blocked accounts on Live, campaigns on Campaigns. */
  counts: Partial<Record<NavId, { value: number; label: string; alert?: boolean }>>;
  onCollapse: () => void;
  onSearch: () => void;
}) {
  const theme = useTheme();
  const rowClass = cn(
    "flex h-[30px] items-center gap-2.5 rounded-md px-2 text-[13px] font-medium text-ink-2 transition-colors hover:bg-hover hover:text-foreground",
    collapsed && "justify-center px-0",
  );
  const label = (text: string) => !collapsed && <span className="truncate">{text}</span>;
  const withTip = (text: string, node: React.ReactElement) =>
    collapsed ? (
      <Tooltip key={text}>
        <TooltipTrigger asChild>{node}</TooltipTrigger>
        <TooltipContent side="right">{text}</TooltipContent>
      </Tooltip>
    ) : (
      node
    );

  return (
    <aside
      className={cn(
        "hidden shrink-0 flex-col bg-frame md:flex",
        collapsed ? "w-14" : "w-[232px]",
      )}
    >
      <div className={cn("flex h-12 items-center gap-2 pr-2 pl-4", collapsed && "justify-center px-0")}>
        <span className="flex size-6 shrink-0 items-center justify-center rounded-md bg-brand text-white">
          <BrandMark />
        </span>
        {!collapsed && (
          <span className="truncate text-[13.5px] font-semibold tracking-[-0.01em]">Distillation Watchdog</span>
        )}
        {!collapsed && hasDashboard(role) && (
          <Tooltip>
            <TooltipTrigger asChild>
              <button
                type="button"
                onClick={onSearch}
                aria-label="Search accounts and campaigns"
                className="ml-auto flex size-7 shrink-0 items-center justify-center rounded-md text-ink-3 transition-colors hover:bg-hover hover:text-foreground"
              >
                <Search className="size-4" />
              </button>
            </TooltipTrigger>
            <TooltipContent side="bottom">
              Search <kbd className="ml-1 font-mono text-ink-3">Ctrl K</kbd>
            </TooltipContent>
          </Tooltip>
        )}
      </div>

      <nav aria-label="Pages" className="grid gap-5 px-2 pt-3">
        {collapsed && hasDashboard(role) &&
          withTip(
            "Search (Ctrl K)",
            <button key="search" type="button" onClick={onSearch} className={cn(rowClass, "w-full")}>
              <Search className="size-4 shrink-0" />
            </button>,
          )}
        {NAV_GROUPS.map((group) => (
          <div key={group.name} className="grid gap-px">
            {!collapsed && <div className="mb-1 px-2 text-[12px] font-medium text-ink-4">{group.name}</div>}
            {items
              .filter((item) => group.ids.includes(item.id))
              .map((item) => {
                const active = isActive(item, route, role);
                const count = counts[item.id];
                return withTip(
                  item.label,
                  <a
                    key={item.id}
                    href={item.href}
                    aria-current={active ? "page" : undefined}
                    aria-label={collapsed ? item.label : undefined}
                    className={cn(rowClass, active && "bg-hover text-foreground")}
                  >
                    <item.icon className={cn("size-4 shrink-0", active ? "text-foreground" : "text-ink-3")} />
                    {label(item.label)}
                    {!collapsed && count && count.value > 0 && (
                      <span
                        title={count.label}
                        className={cn(
                          "ml-auto font-mono text-[11px] tabular-nums",
                          count.alert ? "text-st-block-ink" : "text-ink-4",
                        )}
                      >
                        {formatNumber(count.value)}
                      </span>
                    )}
                    {item.external && !collapsed && (
                      <ArrowUpRight className="ml-auto size-3.5 text-ink-4" aria-label="opens the other server" />
                    )}
                  </a>,
                );
              })}
          </div>
        ))}
      </nav>

      <div className="mt-auto grid gap-0.5 px-2 pb-2">
        {withTip(
          theme === "dark" ? "Light theme" : "Dark theme",
          <button
            key="theme"
            type="button"
            onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
            className={cn(rowClass, "w-full")}
          >
            {theme === "dark" ? <Sun className="size-4 shrink-0" /> : <Moon className="size-4 shrink-0" />}
            {label(theme === "dark" ? "Light theme" : "Dark theme")}
          </button>,
        )}
        {withTip(
          collapsed ? "Open the sidebar" : "Close the sidebar",
          <button key="collapse" type="button" onClick={onCollapse} aria-expanded={!collapsed} className={cn(rowClass, "w-full")}>
            <PanelLeft className="size-4 shrink-0" />
            {label("Close the sidebar")}
          </button>,
        )}
      </div>
    </aside>
  );
}

/** Page links for narrow screens, where the sidebar is hidden. */
function MobileNav({ items, route, role }: { items: NavItem[]; route: Route; role: Role }) {
  const theme = useTheme();
  return (
    <nav aria-label="Pages" className="flex items-center gap-1 overflow-x-auto border-b border-line px-3 py-1.5 md:hidden">
      {items.map((item) => {
        const active = isActive(item, route, role);
        return (
          <a
            key={item.id}
            href={item.href}
            aria-current={active ? "page" : undefined}
            className={cn(
              "flex h-7 shrink-0 items-center gap-1.5 rounded-md px-2 text-[12.5px] text-ink-3",
              active && "bg-hover text-foreground",
            )}
          >
            <item.icon className="size-3.5" />
            {item.label}
          </a>
        );
      })}
      <button
        type="button"
        onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
        aria-label={theme === "dark" ? "Light theme" : "Dark theme"}
        className="ml-auto flex size-7 shrink-0 items-center justify-center rounded-md text-ink-3"
      >
        {theme === "dark" ? <Sun className="size-3.5" /> : <Moon className="size-3.5" />}
      </button>
    </nav>
  );
}

function Stat({ label, value, className }: { label: string; value: string; className?: string }) {
  return (
    <span className={cn("flex items-baseline gap-1.5 whitespace-nowrap", className)}>
      <span className="text-[11.5px] text-ink-3">{label}</span>
      <span className="font-mono text-[12.5px] tabular-nums">{value}</span>
    </span>
  );
}

function Connection({ online }: { online: boolean | null }) {
  const text = online === null ? "Connecting" : online ? "Live" : "Offline";
  return (
    <span className="flex items-center gap-2 whitespace-nowrap" role="status">
      <span className="relative flex size-2">
        {online && <span className="absolute inline-flex size-full animate-ping rounded-full bg-st-normal opacity-50" />}
        <span
          className={cn(
            "relative inline-flex size-2 rounded-full",
            online ? "bg-st-normal" : online === null ? "bg-ink-4" : "bg-st-review",
          )}
        />
      </span>
      <span className={cn("text-[12.5px] font-medium", online === false && "text-st-review-ink")}>{text}</span>
    </span>
  );
}

/** The counters of the whole system. They come from the event stream, one update each second. */
function StatusBar({ stream }: { stream: StreamState }) {
  const summary = stream.summary;
  return (
    <div className="flex h-10 shrink-0 items-center gap-x-5 overflow-hidden border-b border-line px-4 sm:px-6">
      <Connection online={stream.online} />
      {summary && (
        <>
          <span
            className="hidden font-mono text-[12px] whitespace-nowrap text-ink-3 tabular-nums sm:inline"
            title="Simulation clock (UTC). It runs faster than real time."
          >
            {formatSimDateTime(summary.sim_time)} sim
          </span>
          <div className="ml-auto flex items-center gap-x-5">
            <Stat label="req/s" value={formatNumber(Math.round(summary.rps * 10) / 10)} className="hidden lg:flex" />
            <Stat label="served" value={formatCount(summary.served)} className="hidden sm:flex" />
            <Stat label="blocked" value={formatCount(summary.blocked)} />
            {/* Accounts by state live in the Live headline and the sidebar counts, so the bar stays short. */}
            <ul className="hidden items-center gap-3 2xl:flex" aria-label="Accounts in each state">
              {STATES.map((state) => (
                <li key={state} className="flex items-center gap-1.5 whitespace-nowrap" title={STATE_META[state].meaning}>
                  <span className={cn("size-1.5 rounded-full", STATE_META[state].dot)} aria-hidden="true" />
                  <span className="text-[11.5px] text-ink-3">{STATE_META[state].label}</span>
                  <span className="font-mono text-[12.5px] tabular-nums">{formatNumber(summary.accounts?.[state] ?? 0)}</span>
                </li>
              ))}
            </ul>
          </div>
        </>
      )}
    </div>
  );
}

function Page({ route, role }: { route: Route; role: Role }) {
  if (role === "agent") return <AgentChat />;
  // A generator server serves only its own page.
  if (isGenerator(role)) return <Traffic kind={role} role={role} />;
  switch (route.page) {
    case "account":
      return <Account id={route.id} />;
    case "campaigns":
      return <Campaigns selected={route.id ?? null} />;
    case "eval":
      return <Evaluation />;
    case "traffic":
      return <Traffic key={route.kind} kind={route.kind} role={role} />;
    case "agent":
      return <AgentChat />;
    default:
      return <Live />;
  }
}

export default function App() {
  const route = useRoute();
  const role = useQuery({ queryKey: ["role"], queryFn: detectRole, staleTime: Infinity, gcTime: Infinity }).data;
  const stream = useStream(role === "dev" || role === "watchdog");
  const [evidenceId, setEvidenceId] = useState<string | null>(null);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [collapsed, setCollapsed] = useState(() => {
    try {
      return localStorage.getItem(SIDEBAR_KEY) === "1";
    } catch {
      return false;
    }
  });

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k" && role && hasDashboard(role)) {
        event.preventDefault();
        setPaletteOpen((open) => !open);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [role]);

  const toggleSidebar = () => {
    setCollapsed((was) => {
      try {
        localStorage.setItem(SIDEBAR_KEY, was ? "0" : "1");
      } catch {
        // Storage can be blocked. The sidebar still opens and closes for this visit.
      }
      return !was;
    });
  };

  const context = useMemo<AppContextValue | null>(
    () => (role ? { role, stream, openEvidence: setEvidenceId } : null),
    [role, stream],
  );

  // The first paint waits for one local request that tells which server serves the page.
  if (!role || !context) return <div className="h-dvh" aria-busy="true" />;

  const items = navItems(role);
  const dashboard = hasDashboard(role);
  const summary = stream.summary;
  const counts = summary
    ? {
        live: { value: summary.accounts?.block ?? 0, label: "Blocked accounts", alert: true },
        campaigns: { value: summary.campaigns ?? 0, label: "Campaigns found" },
      }
    : {};

  return (
    <AppContext.Provider value={context}>
      <TooltipProvider delayDuration={300}>
        <div className="flex h-dvh bg-frame">
          <Sidebar
            items={items}
            route={route}
            role={role}
            collapsed={collapsed}
            counts={counts}
            onCollapse={toggleSidebar}
            onSearch={() => setPaletteOpen(true)}
          />
          {/* Linear's inset sheet: the page floats on the frame with a hairline and a soft shadow. */}
          <div className="flex min-w-0 flex-1 flex-col overflow-hidden bg-background md:my-2 md:mr-2 md:rounded-xl md:shadow-sheet">
            {dashboard && <StatusBar stream={stream} />}
            <MobileNav items={items} route={route} role={role} />
            {dashboard && stream.online === false && (
              <div className="px-4 pt-3 sm:px-5">
                <Problem>
                  The watchdog is offline. The page tries again every 2 seconds. Start it with{" "}
                  <code className="font-mono">scripts/demo.sh</code>.
                </Problem>
              </div>
            )}
            <main className="min-h-0 flex-1 overflow-y-auto">
              <Page route={route} role={role} />
            </main>
          </div>
        </div>
        {dashboard && <CommandPalette open={paletteOpen} onOpenChange={setPaletteOpen} />}
        {dashboard && <EvidenceDialog id={evidenceId} onClose={() => setEvidenceId(null)} />}
      </TooltipProvider>
    </AppContext.Provider>
  );
}
