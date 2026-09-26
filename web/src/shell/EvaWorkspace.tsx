import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { useResolvedThemeMode } from "@/components/theme/useResolvedThemeMode";
import { Button } from "@/components/ui/button";
import { getOmnigentHostConfig } from "@/lib/host";
import { authenticatedFetch } from "@/lib/identity";
import { useNavigate, useParams, useSearchParams } from "@/lib/routing";

// The shape GET /v1/eva returns (omnigent/airbrx/eva/routes.py `_public`).
export interface EvaBinding {
  label: string;
  host_id: string;
  base_url: string;
  mcp_url: string;
  host_local: boolean;
  fixture: boolean;
  workspace: string | null;
}
interface EvaCatalog {
  agent_id: string | null;
  bindings: EvaBinding[];
}

/**
 * Eva's workspace: the everyday surface. See docs/eva/WORKSPACE.md.
 *
 * Mirrors IrisWorkspace. The landing lists the caller's Eva bindings and starts
 * a session on the bound host; a session route frames her branded app, which
 * the host serves per session. The outreach app stays the deeper surface and
 * the framed app links to it.
 */
export function EvaWorkspace() {
  const { sessionId } = useParams();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const chatMode = searchParams.get("mode") === "chat";
  const mode = useResolvedThemeMode();
  const frame = useRef<HTMLIFrameElement>(null);
  // Captured once: rewriting `src` would reload the frame and lose the chat.
  const [mountTheme] = useState(mode);
  useEffect(() => {
    frame.current?.contentWindow?.postMessage({ evaHostTheme: mode }, window.location.origin);
  }, [mode]);
  useEffect(loadInter, []);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const { data, isLoading } = useQuery({
    queryKey: ["eva-workspace"],
    queryFn: async (): Promise<EvaCatalog> => {
      const response = await authenticatedFetch("/v1/eva");
      if (!response.ok) throw Error("Eva host configuration is unavailable");
      return response.json();
    },
  });

  async function create(binding: EvaBinding) {
    if (!data?.agent_id) return;
    if (binding.host_id && !binding.workspace) {
      setError(
        `The "${binding.label}" binding names a host but no workspace directory. Ask the host operator to add "workspace" to Eva's binding.`,
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
      if (!response.ok) throw Error("Could not start Eva. Check that the bound host is online.");
      const session = await response.json();
      navigate(`/${chatMode ? "c" : "eva"}/${encodeURIComponent(session.id)}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not start Eva");
    } finally {
      setBusy(false);
    }
  }

  // Embedded hosts proxy the API through a fetcher an iframe cannot use; the
  // same reason IrisWorkspace gives. Offer the native chat, same session.
  if (sessionId && getOmnigentHostConfig().fetcher)
    return (
      <main className="mx-auto flex w-full max-w-xl flex-col gap-4 p-6">
        <h1 className="font-semibold text-xl">Eva workspace</h1>
        <p role="alert">
          The Eva workspace is served by the Omnigent host itself, and this embedded host reaches
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
      <iframe
        ref={frame}
        title="Eva workspace"
        // oxlint-disable-next-line iframe-missing-sandbox -- Same-origin host UI needs scripts and session cookies.
        sandbox="allow-scripts allow-same-origin allow-forms allow-downloads allow-popups allow-top-navigation-by-user-activation"
        className="h-full min-h-0 w-full flex-1 border-0"
        onLoad={() =>
          frame.current?.contentWindow?.postMessage({ evaHostTheme: mode }, window.location.origin)
        }
        src={`/v1/eva/sessions/${encodeURIComponent(sessionId)}/ui/?theme=${mountTheme}`}
      />
    );
  return (
    <div
      className="flex min-h-0 w-full flex-1 overflow-y-auto bg-[#F0EFED] text-[#1A1A1A] antialiased dark:bg-[#121212] dark:text-[#E0E0E0]"
      style={{ fontFamily: "Inter, system-ui, -apple-system, sans-serif" }}
    >
      <main className="mx-auto flex w-full max-w-xl flex-col gap-5 px-4 py-12">
        <div className="flex items-center gap-3">
          <img
            src="/v1/eva/portrait"
            alt=""
            className="size-12 rounded-xl bg-[#F5F5F5] object-cover dark:bg-[#242424]"
          />
          <div>
            <p className="font-bold text-[#8A8A8A] text-[10px] uppercase tracking-[0.12em]">
              airbrx outreach agent
            </p>
            <h1 className="font-extrabold text-2xl tracking-[-0.02em]">Eva workspace</h1>
          </div>
        </div>
        <p className="text-[#505050] text-sm leading-relaxed dark:text-[#A0A0A0]">
          One workspace for the airbrx lead list: Eva's chat, and the outreach app's leads, pool,
          accounts, analytics, guardrails, sync and settings as tabs, with Eva docked beside
          whichever one is open.
        </p>
        {isLoading ? (
          <p role="status" className="text-[#8A8A8A] text-sm">
            Loading Eva…
          </p>
        ) : !data?.agent_id || !data.bindings.length ? (
          <p role="alert" className="text-sm">
            Eva has no binding for you on this host. Ask the host operator to complete the Eva
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
                    {binding.fixture ? "Sample data for trying Eva out" : "Your leads and pool"}
                  </span>
                </span>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => void create(binding)}
                  className="shrink-0 rounded-xl bg-[#FD6C1D] px-5 py-2 font-semibold text-sm text-white transition-colors hover:bg-[#E65A0D] disabled:opacity-50"
                >
                  {chatMode ? "Start chat" : "Start"}
                </button>
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

/** A binding as a rep reads it: its label, never the loopback URL behind it. */
export function displayName(binding: EvaBinding): string {
  const label = binding.label.trim() || "Eva";
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
