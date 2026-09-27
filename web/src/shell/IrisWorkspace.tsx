import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { useResolvedThemeMode } from "@/components/theme/useResolvedThemeMode";
import { Button } from "@/components/ui/button";
import { getOmnigentHostConfig } from "@/lib/host";
import { authenticatedFetch } from "@/lib/identity";
import { useNavigate, useParams, useSearchParams } from "@/lib/routing";
import { IrisAccountView, type IrisAccount } from "./IrisAccountView";

// The per-binding shape GET /v1/iris returns (omnigent/airbrx/iris/routes.py).
interface IrisBinding {
  tenant_id: string;
  name?: string;
  host_id: string;
  workspace: string;
  fixture: boolean;
  /** Whether the execution host behind this binding is connected right now.
   *
   * `null` means the server could not tell (and `undefined` that it predates
   * the field). Neither is offline: only `false` refuses the drill-in. Reading
   * unknown as offline would let a missing lookup grey out every tenant. */
  host_online?: boolean | null;
}
interface IrisCatalog {
  agent_id: string | null;
  bindings: IrisBinding[];
}
/** The fields of GET /v1/sessions this page reads. */
interface IrisSession {
  id: string;
  host_id?: string | null;
  workspace?: string | null;
  updated_at?: number;
}

/** What the v2 workspace frame posts to open another tenant (WORKSPACE_V2.md, 4). */
export const OPEN_TENANT = "iris.openTenant";

/**
 * The newest of the caller's sessions on this tenant, or nothing.
 *
 * Matched on host AND workspace: both production tenants share one execution
 * host, so a match on the host alone resumes the wrong tenant. `sessions` is
 * newest first, as the recent-sessions query asks for it.
 */
export function resumableSession(
  binding: Pick<IrisBinding, "host_id" | "workspace">,
  sessions: IrisSession[] | undefined,
): IrisSession | undefined {
  return sessions?.find((s) => s.host_id === binding.host_id && s.workspace === binding.workspace);
}

export { shortId } from "./IrisAccountView";

export function IrisWorkspace() {
  const { sessionId } = useParams();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const chatMode = searchParams.get("mode") === "chat";
  const mode = useResolvedThemeMode();
  const frame = useRef<HTMLIFrameElement>(null);
  useEffect(loadInter, []);
  // The workspace mounts with the shell's appearance in its URL and is told
  // about later changes by message. Rewriting `src` would reload the iframe
  // and discard the conversation inside it, which is the one thing on this
  // page that cannot be recovered — so the mount value is captured once.
  const [mountTheme] = useState(mode);
  // next-themes resolves after first paint, so the mount value can be a guess.
  // Posting on every change (and again on load, since a message sent before
  // the document exists goes nowhere) corrects it without a reload.
  useEffect(() => {
    frame.current?.contentWindow?.postMessage({ irisHostTheme: mode }, window.location.origin);
  }, [mode]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const { data, isLoading } = useQuery({
    queryKey: ["iris-workspace"],
    queryFn: async (): Promise<IrisCatalog> => {
      const response = await authenticatedFetch("/v1/iris");
      if (!response.ok) throw Error("Iris host configuration is unavailable");
      return response.json();
    },
  });
  // Deterministic on the server: no model runs to build this list. It is a
  // separate query so a triage outage never hides the picker — the view
  // still lists every binding for drill-in and shows the error beside it.
  const account = useQuery({
    queryKey: ["iris-account"],
    enabled: Boolean(data?.agent_id && data.bindings.length),
    queryFn: async (): Promise<IrisAccount> => {
      const response = await authenticatedFetch("/v1/iris/account");
      if (!response.ok) {
        const detail = await response
          .json()
          .then((body: { detail?: unknown }) =>
            typeof body.detail === "string" ? body.detail : "",
          )
          .catch(() => "");
        throw Error(detail || `The account could not be read (HTTP ${response.status})`);
      }
      return response.json();
    },
  });
  // The caller's recent Iris sessions, newest first, so a visit can go back
  // to a tenant instead of adding a new session every time. Read only on the
  // landing; a failure just means there is nothing to resume.
  const recent = useQuery({
    queryKey: ["iris-recent-sessions", data?.agent_id],
    enabled: Boolean(!sessionId && data?.agent_id),
    queryFn: async (): Promise<IrisSession[]> => {
      const params = new URLSearchParams({
        agent_id: data?.agent_id ?? "",
        limit: "50",
        sort_by: "updated_at",
        visibility: "mine",
      });
      try {
        const response = await authenticatedFetch(`/v1/sessions?${params}`);
        if (!response.ok) return [];
        return ((await response.json()) as { data?: IrisSession[] }).data ?? [];
      } catch {
        return [];
      }
    },
  });
  const resumable: Record<string, string> = {};
  const resumeIds: Record<string, string> = {};
  for (const binding of data?.bindings ?? []) {
    const last = resumableSession(binding, recent.data);
    if (!last) continue;
    resumable[binding.tenant_id] = lastUsed(last);
    resumeIds[binding.tenant_id] = last.id;
  }
  const resume = (tenantId: string) => {
    const id = resumeIds[tenantId];
    if (id) navigate(`/${chatMode ? "c" : "iris"}/${encodeURIComponent(id)}`);
  };
  async function create(tenantId: string) {
    const binding = data?.bindings.find((b) => b.tenant_id === tenantId);
    if (!data?.agent_id || !binding) return;
    // The row is already disabled for a known-offline host, but this is the
    // last stop before POST /v1/sessions, which would refuse with a 400 and a
    // less specific message. Only `false` refuses: `null` and `undefined` are
    // "could not tell", and a session on a host we cannot see may well work.
    if (binding.host_online === false) {
      setError(`Host "${binding.host_id}" is offline, so Iris cannot start there yet.`);
      return;
    }
    setBusy(true);
    setError("");
    try {
      // The same registered-agent session endpoint used by the landing picker.
      const response = await authenticatedFetch("/v1/sessions", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          agent_id: data.agent_id,
          host_id: binding.host_id,
          workspace: binding.workspace,
        }),
      });
      if (!response.ok)
        throw Error("Could not start Iris. Check that the selected host is online.");
      const session = await response.json();
      navigate(`/${chatMode ? "c" : "iris"}/${encodeURIComponent(session.id)}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not start Iris");
    } finally {
      setBusy(false);
    }
  }
  // The v2 workspace's Accounts tab asks the shell to open another tenant.
  // It never switches tenant inside its own session: the shell opens a NEW
  // session through the same create() as the landing, and only for a
  // message from its own frame, on its own origin, naming a bound tenant.
  // The pinned UI never posts this, so without v2 the listener stays idle.
  const openTenant = useRef(create);
  openTenant.current = create;
  // One open at a time. A burst of messages (a double click in the frame, or
  // a page that posts in a loop) arrives before React re-renders, so `busy`
  // state would still read false for every one of them; a ref does not.
  const opening = useRef(false);
  useEffect(() => {
    if (!sessionId) return;
    const bound = new Set(data?.bindings.map((b) => b.tenant_id) ?? []);
    function onMessage(event: MessageEvent) {
      if (event.origin !== window.location.origin) return;
      if (!frame.current || event.source !== frame.current.contentWindow) return;
      const message: unknown = event.data;
      if (typeof message !== "object" || message === null) return;
      const { type, tenant_id: tenantId } = message as { type?: unknown; tenant_id?: unknown };
      if (type !== OPEN_TENANT || typeof tenantId !== "string" || !bound.has(tenantId)) return;
      if (opening.current) return;
      opening.current = true;
      void openTenant.current(tenantId).finally(() => {
        opening.current = false;
      });
    }
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, [sessionId, data]);
  // Embedded, the host proxies the whole API behind a path prefix and auth that
  // only its `fetcher` can satisfy — and an <iframe src> cannot be routed
  // through a JavaScript function. The workspace is a hosted page, not a bundle
  // we ship, so it is genuinely unavailable in that target. Say so and point at
  // the native chat, which is the same session; mounting the frame anyway would
  // resolve against the wrong origin and show the user an empty rectangle.
  if (sessionId && getOmnigentHostConfig().fetcher)
    return (
      <main className="mx-auto flex w-full max-w-xl flex-col gap-4 p-6">
        <h1 className="font-semibold text-xl">Iris workspace</h1>
        <p role="alert">
          The Iris workspace is served by the Omnigent host itself, and this embedded host reaches
          the API through its own proxy, which a framed page cannot use. Open Omnigent directly to
          use the workspace.
        </p>
        <Button onClick={() => navigate(`/c/${encodeURIComponent(sessionId)}`)}>
          Open native chat instead
        </Button>
        <p>It is the same Iris session, with the same history.</p>
      </main>
    );
  // The slot before the frame is always rendered (null when there is no
  // error) so the iframe keeps its position: moving it would remount it and
  // reload the workspace, losing the conversation inside.
  if (sessionId)
    return (
      <>
        {error ? (
          <div
            role="alert"
            className="flex shrink-0 items-center justify-between gap-3 border-b bg-[#FEF2F2] px-4 py-2 text-[#991B1B] text-sm dark:bg-[#2A1515] dark:text-[#FCA5A5]"
          >
            <span>Iris could not open that tenant in a new session. {error}</span>
            <button type="button" className="shrink-0 underline" onClick={() => setError("")}>
              Dismiss
            </button>
          </div>
        ) : null}
        <iframe
          ref={frame}
          title="Iris workspace"
          // oxlint-disable-next-line iframe-missing-sandbox -- Pinned same-origin host UI needs scripts and session cookies.
          sandbox="allow-scripts allow-same-origin allow-forms allow-downloads allow-popups allow-top-navigation-by-user-activation"
          className="h-full min-h-0 w-full flex-1 border-0"
          onLoad={() =>
            frame.current?.contentWindow?.postMessage(
              { irisHostTheme: mode },
              window.location.origin,
            )
          }
          src={`/v1/iris/sessions/${encodeURIComponent(sessionId)}/ui/?theme=${mountTheme}`}
        />
      </>
    );
  return (
    <div
      className="flex min-h-0 w-full flex-1 overflow-y-auto bg-[#F0EFED] text-[#1A1A1A] antialiased dark:bg-[#121212] dark:text-[#E0E0E0]"
      style={{ fontFamily: "Inter, system-ui, -apple-system, sans-serif" }}
    >
      <main className="mx-auto flex w-full max-w-3xl flex-col gap-5 px-4 pt-20 pb-12 md:pt-12">
        <div className="flex items-center gap-3">
          <img
            src="/v1/iris/portrait"
            alt=""
            className="size-12 rounded-xl bg-[#F5F5F5] object-cover dark:bg-[#242424]"
          />
          <div>
            <p className="font-bold text-[#8A8A8A] text-[10px] uppercase tracking-[0.12em]">
              airbrx cache intelligence
            </p>
            <h1 className="font-extrabold text-2xl tracking-[-0.02em]">Iris workspace</h1>
          </div>
        </div>
        <p className="text-[#505050] text-sm leading-relaxed dark:text-[#A0A0A0]">
          Your bound tenants, ranked by cache misses in each one's newest complete capture. Iris
          runs only after you open one. Monitoring is unavailable.
        </p>
        {isLoading ? (
          <p role="status" className="text-[#8A8A8A] text-sm">
            Loading Iris…
          </p>
        ) : !data?.agent_id || !data.bindings.length ? (
          <p role="alert" className="text-sm">
            Iris has no authorized host binding. Ask the host operator to complete the Iris setup.
          </p>
        ) : (
          <>
            <IrisAccountView
              tenants={data.bindings}
              account={account.data ?? null}
              accountError={account.error instanceof Error ? account.error.message : ""}
              busy={busy}
              loading={account.isPending}
              openLabel={chatMode ? "Start chat" : "Open workspace"}
              onOpen={(tenantId) => void create(tenantId)}
              resumable={resumable}
              onResume={resume}
            />
            <p className="text-[#8A8A8A] text-xs leading-relaxed">
              A session reads one tenant. Resume goes back to your last session on that tenant; New
              starts another. “Open native chat” continues the same conversation.
            </p>
          </>
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

/** "Last used Sep 26, 10:14 AM" for a session. */
function lastUsed(session: IrisSession): string {
  if (!session.updated_at) return "Your last session is ready to resume";
  const when = new Date(session.updated_at * 1000).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
  return `Last used ${when}`;
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
