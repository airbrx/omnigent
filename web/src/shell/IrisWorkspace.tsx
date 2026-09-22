import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { useResolvedThemeMode } from "@/components/theme/useResolvedThemeMode";
import { Button } from "@/components/ui/button";
import { getOmnigentHostConfig } from "@/lib/host";
import { authenticatedFetch } from "@/lib/identity";
import { useNavigate, useParams, useSearchParams } from "@/lib/routing";
import { IrisAccountView, type IrisAccount } from "./IrisAccountView";

interface IrisBinding {
  tenant_id: string;
  name?: string;
  host_id: string;
  workspace: string;
  fixture: boolean;
}
interface IrisCatalog {
  agent_id: string | null;
  bindings: IrisBinding[];
}

export { shortId } from "./IrisAccountView";

export function IrisWorkspace() {
  const { sessionId } = useParams();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const chatMode = searchParams.get("mode") === "chat";
  const mode = useResolvedThemeMode();
  const frame = useRef<HTMLIFrameElement>(null);
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
          .then((body: { detail?: unknown }) => (typeof body.detail === "string" ? body.detail : ""))
          .catch(() => "");
        throw Error(detail || `The account could not be read (HTTP ${response.status})`);
      }
      return response.json();
    },
  });
  async function create(tenantId: string) {
    const binding = data?.bindings.find((b) => b.tenant_id === tenantId);
    if (!data?.agent_id || !binding) return;
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
  if (sessionId)
    return (
      <iframe
        ref={frame}
        title="Iris workspace"
        // oxlint-disable-next-line iframe-missing-sandbox -- Pinned same-origin host UI needs scripts and session cookies.
        sandbox="allow-scripts allow-same-origin allow-forms allow-downloads allow-top-navigation-by-user-activation"
        className="h-full min-h-0 w-full flex-1 border-0"
        onLoad={() =>
          frame.current?.contentWindow?.postMessage({ irisHostTheme: mode }, window.location.origin)
        }
        src={`/v1/iris/sessions/${encodeURIComponent(sessionId)}/ui/?theme=${mountTheme}`}
      />
    );
  return (
    <main className="mx-auto flex w-full max-w-xl flex-col gap-4 p-6">
      <h1 className="font-semibold text-xl">Iris workspace</h1>
      <p>
        Your bound tenants, ranked by cache misses in each one's newest complete capture. Iris
        runs only after you open one. Monitoring is unavailable.
      </p>
      {isLoading ? (
        <p role="status">Loading Iris…</p>
      ) : !data?.agent_id || !data.bindings.length ? (
        <p role="alert">
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
          />
          <p>
            Use “Refresh from host” inside the workspace to collect the first overview. “Open native
            chat” continues the same conversation.
          </p>
        </>
      )}
      {error && <p role="alert">{error}</p>}
    </main>
  );
}
