import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { useResolvedThemeMode } from "@/components/theme/useResolvedThemeMode";
import { Button } from "@/components/ui/button";
import { authenticatedFetch } from "@/lib/identity";
import { useNavigate, useParams, useSearchParams } from "@/lib/routing";

interface IrisBinding {
  tenant_id: string;
  host_id: string;
  workspace: string;
  fixture: boolean;
}
interface IrisCatalog {
  agent_id: string | null;
  bindings: IrisBinding[];
}

export function IrisWorkspace() {
  const { sessionId } = useParams();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const chatMode = searchParams.get("mode") === "chat";
  const [selected, setSelected] = useState("");
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
  async function create() {
    const binding = data?.bindings.find((b) => b.tenant_id === selected);
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
        Choose the authorized tenant for this session. Iris analyzes evidence and prepares proposals
        for review. Monitoring is unavailable.
      </p>
      {isLoading ? (
        <p role="status">Loading Iris…</p>
      ) : !data?.agent_id || !data.bindings.length ? (
        <p role="alert">
          Iris has no authorized host binding. Ask the host operator to complete the Iris setup.
        </p>
      ) : (
        <>
          <label htmlFor="iris-tenant">Tenant</label>
          <select
            id="iris-tenant"
            className="rounded border bg-background p-2"
            value={selected}
            onChange={(e) => setSelected(e.target.value)}
          >
            <option value="">Select a tenant</option>
            {data.bindings.map((b) => (
              <option key={b.tenant_id} value={b.tenant_id}>
                {b.tenant_id}
                {b.fixture ? " — synthetic fixture" : " — read-only"}
              </option>
            ))}
          </select>
          <Button disabled={!selected || busy} onClick={create}>
            {busy ? "Starting Iris…" : chatMode ? "Start chat" : "Open workspace"}
          </Button>
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
