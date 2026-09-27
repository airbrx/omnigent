import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { useResolvedThemeMode } from "@/components/theme/useResolvedThemeMode";
import { Button } from "@/components/ui/button";
import { useSessionHostOnline } from "@/hooks/RunnerHealthProvider";
import { useHosts } from "@/hooks/useHosts";
import { getOmnigentHostConfig } from "@/lib/host";
import { authenticatedFetch } from "@/lib/identity";
import { useNavigate, useParams, useSearchParams } from "@/lib/routing";

// The shape GET /v1/tally returns (omnigent/airbrx/tally/routes.py `_public`).
export interface TallyBinding {
  label: string;
  host_id: string;
  base_url: string;
  mcp_url: string;
  host_local: boolean;
  fixture: boolean;
  workspace: string | null;
}
/** The fields of GET /v1/sessions this page reads. */
interface TallySession {
  id: string;
  host_id?: string | null;
  updated_at?: number;
}
interface TallyCatalog {
  agent_id: string | null;
  bindings: TallyBinding[];
}

/**
 * Tally's workspace: the everyday surface. See docs/tally/RUNBOOK.md.
 *
 * A copy of EvaWorkspace with Tally's content. The landing lists the caller's
 * Tally bindings and starts a session (on the bound host when the binding names
 * one); a session route frames her branded app, which the host serves per
 * session. The portal stays the deeper surface and the framed app tabs to it.
 */
export function TallyWorkspace() {
  const { sessionId } = useParams();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const chatMode = searchParams.get("mode") === "chat";
  const mode = useResolvedThemeMode();
  const frame = useRef<HTMLIFrameElement>(null);
  // Captured once: rewriting `src` would reload the frame and lose the chat.
  const [mountTheme] = useState(mode);
  useEffect(() => {
    frame.current?.contentWindow?.postMessage({ tallyHostTheme: mode }, window.location.origin);
  }, [mode]);
  useEffect(loadInter, []);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  // A start that failed because the bound host could not take it: the
  // fallback signal when the host list has not (yet) said "offline".
  const [startFailed, setStartFailed] = useState(false);
  // Host-tunnel liveness for an open session (null: not host-bound).
  const sessionHostOnline = useSessionHostOnline(sessionId);
  const [frameKey, setFrameKey] = useState(0);
  const { data, isLoading } = useQuery({
    queryKey: ["tally-workspace"],
    queryFn: async (): Promise<TallyCatalog> => {
      const response = await authenticatedFetch("/v1/tally");
      if (!response.ok) throw Error("Tally host configuration is unavailable");
      return response.json();
    },
  });

  // Tally's recent sessions, newest first, so Start does not always mean "new":
  // otherwise every visit adds a "New session" row to the sidebar.
  const recent = useQuery({
    queryKey: ["tally-recent-sessions", data?.agent_id],
    enabled: Boolean(!sessionId && data?.agent_id),
    queryFn: async (): Promise<TallySession[]> => {
      const params = new URLSearchParams({
        agent_id: data?.agent_id ?? "",
        limit: "20",
        sort_by: "updated_at",
        visibility: "mine",
      });
      const response = await authenticatedFetch(`/v1/sessions?${params}`);
      if (!response.ok) return [];
      return ((await response.json()) as { data?: TallySession[] }).data ?? [];
    },
  });
  // Tally runs on a bound execution host (Abram's Mac), offline whenever the
  // Mac sleeps. /v1/hosts carries each host's online/offline status.
  const hosts = useHosts({
    enabled: Boolean(!sessionId && data?.bindings.some((b) => b.host_id)),
  });
  const hostOffline = (binding: TallyBinding) =>
    Boolean(
      binding.host_id &&
      hosts.data?.find((h) => h.host_id === binding.host_id)?.status === "offline",
    );
  const notConnected =
    startFailed || Boolean(data?.bindings.length && data.bindings.every(hostOffline));
  const retry = () => {
    setStartFailed(false);
    setError("");
    void hosts.refetch();
  };
  const lastSession = (binding: TallyBinding) =>
    recent.data?.find((s) => !binding.host_id || s.host_id === binding.host_id);
  const open = (id: string) => navigate(`/${chatMode ? "c" : "tally"}/${encodeURIComponent(id)}`);

  async function create(binding: TallyBinding) {
    if (!data?.agent_id) return;
    if (binding.host_id && !binding.workspace) {
      setError(
        `The "${binding.label}" binding names a host but no workspace directory. Ask the host operator to add "workspace" to Tally's binding.`,
      );
      return;
    }
    setBusy(true);
    setError("");
    try {
      const body: Record<string, string> = { agent_id: data.agent_id };
      if (binding.host_id) {
        body.host_id = binding.host_id;
        body.workspace = binding.workspace ?? "";
      }
      const response = await authenticatedFetch("/v1/sessions", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!response.ok) {
        // 409 is the server's "host offline"; 5xx means it could not reach it.
        if (binding.host_id && (response.status === 409 || response.status >= 500)) {
          setStartFailed(true);
          return;
        }
        throw Error("Could not start Tally. Check that her host is online.");
      }
      const session = await response.json();
      navigate(`/${chatMode ? "c" : "tally"}/${encodeURIComponent(session.id)}`);
    } catch (e) {
      // A network failure on a host-bound start is the host being unreachable.
      if (binding.host_id && !(e instanceof Error && e.message.startsWith("Could not start"))) {
        setStartFailed(true);
        return;
      }
      setError(e instanceof Error ? e.message : "Could not start Tally");
    } finally {
      setBusy(false);
    }
  }

  // Embedded hosts proxy the API through a fetcher an iframe cannot use; the
  // same reason IrisWorkspace gives. Offer the native chat, same session.
  if (sessionId && getOmnigentHostConfig().fetcher)
    return (
      <main className="mx-auto flex w-full max-w-xl flex-col gap-4 p-6">
        <h1 className="font-semibold text-xl">Tally workspace</h1>
        <p role="alert">
          The Tally workspace is served by the Omnigent host itself, and this embedded host reaches
          the API through its own proxy, which a framed page cannot use. Open Omnigent directly to
          use the workspace.
        </p>
        <Button onClick={() => navigate(`/c/${encodeURIComponent(sessionId)}`)}>
          Open native chat instead
        </Button>
      </main>
    );
  if (sessionId)
    return (
      <>
        {/* AppShell lays its ChatHeader over <main>: absolute, top-0, z-30,
            transparent, h-14 (md:h-12). Over a framed page it takes every
            click in that band, which is where Tally's tab bar and Refresh sit.
            The frame starts below it instead, as Eva's and Iris's do. */}
        <div
          aria-hidden="true"
          data-tally-header-clearance=""
          className="h-14 shrink-0 bg-[#F0EFED] md:h-12 dark:bg-[#121212]"
        />
        {sessionHostOnline === false && (
          <NotConnectedBanner onRetry={() => setFrameKey((k) => k + 1)} />
        )}
        <iframe
          key={frameKey}
          ref={frame}
          title="Tally workspace"
          // oxlint-disable-next-line iframe-missing-sandbox -- Same-origin host UI needs scripts and session cookies.
          sandbox="allow-scripts allow-same-origin allow-forms allow-downloads allow-popups allow-top-navigation-by-user-activation"
          className="h-full min-h-0 w-full flex-1 border-0"
          onLoad={() =>
            frame.current?.contentWindow?.postMessage(
              { tallyHostTheme: mode },
              window.location.origin,
            )
          }
          src={`/v1/tally/sessions/${encodeURIComponent(sessionId)}/ui/?theme=${mountTheme}`}
        />
      </>
    );
  if (notConnected)
    return (
      <>
        <div
          aria-hidden="true"
          data-tally-header-clearance=""
          className="h-14 shrink-0 bg-[#F0EFED] md:h-12 dark:bg-[#121212]"
        />
        <NotConnectedBanner onRetry={retry} busy={hosts.isFetching} />
        <PortalTabs />
      </>
    );
  return (
    <div
      className="flex min-h-0 w-full flex-1 overflow-y-auto bg-[#F0EFED] text-[#1A1A1A] antialiased dark:bg-[#121212] dark:text-[#E0E0E0]"
      style={{ fontFamily: "Inter, system-ui, -apple-system, sans-serif" }}
    >
      <main className="mx-auto flex w-full max-w-xl flex-col gap-5 px-4 pt-20 pb-12 md:pt-12">
        <div className="flex items-center gap-3">
          <img
            src="/v1/tally/portrait"
            alt=""
            className="size-12 rounded-xl bg-[#F5F5F5] object-cover dark:bg-[#242424]"
          />
          <div>
            <p className="font-bold text-[#8A8A8A] text-[10px] uppercase tracking-[0.12em]">
              airbrx control plane agent
            </p>
            <h1 className="font-extrabold text-2xl tracking-[-0.02em]">Tally workspace</h1>
          </div>
        </div>
        <p className="text-[#505050] text-sm leading-relaxed dark:text-[#A0A0A0]">
          One workspace for the airbrx control plane: Tally's read of agents, costs, coverage,
          decisions and blockers, and the portal's analytics, agent management and sprint board as
          tabs, with Tally docked beside whichever one is open. Tally is read-only.
        </p>
        {isLoading ? (
          <p role="status" className="text-[#8A8A8A] text-sm">
            Loading Tally…
          </p>
        ) : !data?.agent_id || !data.bindings.length ? (
          <p role="alert" className="text-sm">
            Tally has no binding for you on this host. Ask the host operator to complete the Tally
            setup.
          </p>
        ) : (
          <ul className="flex flex-col gap-3">
            {data.bindings.map((binding) => (
              <li
                key={binding.label}
                className="flex items-center justify-between gap-3 rounded-2xl border border-[#E8E8E8] bg-white px-5 py-4 shadow-[0_2px_8px_rgba(0,0,0,0.06),0_0_1px_rgba(0,0,0,0.08)] dark:border-[#333333] dark:bg-[#1A1A1A]"
              >
                <span className="min-w-0">
                  <span className="block font-semibold text-sm">{displayName(binding)}</span>
                  <span className="block text-[#8A8A8A] text-xs">
                    {lastUsed(lastSession(binding)) ??
                      (binding.fixture
                        ? "Sample data for trying Tally out"
                        : "The control plane, read-only")}
                  </span>
                </span>
                <span className="flex shrink-0 gap-2">
                  {(() => {
                    const last = lastSession(binding);
                    if (!last)
                      return (
                        <button
                          type="button"
                          disabled={busy}
                          onClick={() => void create(binding)}
                          className={PRIMARY}
                        >
                          {chatMode ? "Start chat" : "Start"}
                        </button>
                      );
                    return (
                      <>
                        <button
                          type="button"
                          disabled={busy}
                          onClick={() => void create(binding)}
                          className={SECONDARY}
                        >
                          New
                        </button>
                        <button
                          type="button"
                          disabled={busy}
                          onClick={() => open(last.id)}
                          className={PRIMARY}
                        >
                          Resume
                        </button>
                      </>
                    );
                  })()}
                </span>
              </li>
            ))}
          </ul>
        )}
        {error && (
          <p role="alert" className="text-[#DC2626] text-sm">
            {error}
          </p>
        )}
      </main>
    </div>
  );
}

/** Tally's host is offline: chat is unavailable, the portal is not. */
function NotConnectedBanner({ onRetry, busy = false }: { onRetry: () => void; busy?: boolean }) {
  return (
    <section
      role="status"
      aria-label="Agent not connected"
      className="flex shrink-0 items-center justify-between gap-3 border-[#E8E8E8] border-b bg-white px-5 py-3 text-[#1A1A1A] dark:border-[#333333] dark:bg-[#1A1A1A] dark:text-[#E0E0E0]"
      style={{ fontFamily: "Inter, system-ui, -apple-system, sans-serif" }}
    >
      <span className="flex min-w-0 items-start gap-3">
        <span
          aria-hidden="true"
          className="mt-1.5 inline-block size-2 shrink-0 rounded-full bg-[#8A8A8A]"
        />
        <span className="min-w-0">
          <span className="block font-semibold text-sm">Agent not connected</span>
          <span className="block text-[#505050] text-xs leading-relaxed dark:text-[#A0A0A0]">
            Tally's host is offline, so chat is unavailable. The portal tabs still work.
          </span>
        </span>
      </span>
      <button type="button" disabled={busy} onClick={onRetry} className={SECONDARY}>
        Retry
      </button>
    </section>
  );
}

// The portal's own pages, framed through Omnigent's same-origin gateway proxy.
// The same three tabs Tally's app shows (omnigent/airbrx/tally/ui/app.js); they
// need the portal, not the agent.
const PORTAL_TABS = [
  { id: "analytics", label: "Analytics", path: "/gateway/app/#overview" },
  { id: "agents", label: "Agent management", path: "/gateway/app/#agents" },
  { id: "board", label: "Sprint board", path: "/gateway/app/#board" },
] as const;

function PortalTabs() {
  const [active, setActive] = useState<(typeof PORTAL_TABS)[number]["id"]>("analytics");
  const tab = PORTAL_TABS.find((t) => t.id === active) ?? PORTAL_TABS[0];
  return (
    <>
      <div
        role="tablist"
        aria-label="Portal"
        className="flex shrink-0 gap-1 border-[#E8E8E8] border-b bg-[#F0EFED] px-4 py-2 dark:border-[#333333] dark:bg-[#121212]"
        style={{ fontFamily: "Inter, system-ui, -apple-system, sans-serif" }}
      >
        {PORTAL_TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            role="tab"
            aria-selected={t.id === active}
            onClick={() => setActive(t.id)}
            className={`rounded-lg px-3 py-1.5 font-medium text-sm transition-colors ${
              t.id === active
                ? "bg-white text-[#1A1A1A] shadow-[0_1px_2px_rgba(0,0,0,0.06)] dark:bg-[#242424] dark:text-[#E0E0E0]"
                : "text-[#505050] hover:bg-white/60 dark:text-[#A0A0A0] dark:hover:bg-[#1A1A1A]"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>
      <iframe
        role="tabpanel"
        title={`Portal: ${tab.label}`}
        // oxlint-disable-next-line iframe-missing-sandbox -- Same-origin portal needs scripts and session cookies.
        sandbox="allow-scripts allow-same-origin allow-forms allow-downloads allow-popups allow-top-navigation-by-user-activation"
        className="h-full min-h-0 w-full flex-1 border-0"
        src={tab.path}
      />
    </>
  );
}

const PRIMARY =
  "rounded-xl bg-[#FD6C1D] px-5 py-2 font-semibold text-sm text-white transition-colors hover:bg-[#E65A0D] disabled:opacity-50";
const SECONDARY =
  "rounded-xl border-[1.5px] border-[#D4D4D4] bg-white px-4 py-2 font-medium text-[#1A1A1A] text-sm transition-colors hover:bg-[#F5F5F5] disabled:opacity-50 dark:border-[#444444] dark:bg-[#1A1A1A] dark:text-[#E0E0E0] dark:hover:bg-[#242424]";

/** "Last used Sep 26, 10:14 AM" for a session, or nothing when there is none. */
function lastUsed(session: TallySession | undefined): string | undefined {
  if (!session?.updated_at) return session ? "Your last session is ready to resume" : undefined;
  const when = new Date(session.updated_at * 1000).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
  return `Last used ${when}`;
}

/** A binding as a rep reads it: its label, never the loopback URL behind it. */
export function displayName(binding: TallyBinding): string {
  const label = binding.label.trim() || "Tally";
  return label.charAt(0).toUpperCase() + label.slice(1);
}

const INTER =
  "https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap";

/** Inter is the airbrx typeface; the rest of the shell does not load it. */
function loadInter() {
  if (document.querySelector(`link[href="${INTER}"]`)) return;
  const link = document.createElement("link");
  link.rel = "stylesheet";
  link.href = INTER;
  document.head.appendChild(link);
}
