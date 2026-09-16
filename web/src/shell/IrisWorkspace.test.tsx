import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { authenticatedFetch } from "@/lib/identity";
import { IrisWorkspace } from "./IrisWorkspace";

const routing = vi.hoisted(() => ({
  navigate: vi.fn(),
  params: {} as { sessionId?: string },
  search: new URLSearchParams(),
}));
vi.mock("@/lib/routing", () => ({
  useNavigate: () => routing.navigate,
  useParams: () => routing.params,
  useSearchParams: () => [routing.search],
}));
vi.mock("@/lib/identity", () => ({ authenticatedFetch: vi.fn() }));
const theme = vi.hoisted(() => ({ mode: "dark" as "light" | "dark" }));
vi.mock("@/components/theme/useResolvedThemeMode", () => ({
  useResolvedThemeMode: () => theme.mode,
}));

beforeEach(() => {
  vi.clearAllMocks();
  routing.params = {};
  routing.search = new URLSearchParams();
  theme.mode = "dark";
});
function show() {
  return render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <IrisWorkspace />
    </QueryClientProvider>,
  );
}

it("requires explicit tenant selection and uses native session creation", async () => {
  vi.mocked(authenticatedFetch)
    .mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          agent_id: "registered-iris",
          bindings: [
            {
              tenant_id: "fixture",
              host_id: "approved-host",
              workspace: "/approved",
              fixture: true,
            },
          ],
        }),
      ),
    )
    .mockResolvedValueOnce(new Response(JSON.stringify({ id: "native-session" }), { status: 201 }));
  show();
  expect(await screen.findByRole("button", { name: "Open workspace" })).toBeDisabled();
  fireEvent.change(screen.getByLabelText("Tenant"), { target: { value: "fixture" } });
  fireEvent.click(screen.getByRole("button", { name: "Open workspace" }));
  await waitFor(() => expect(routing.navigate).toHaveBeenCalledWith("/iris/native-session"));
  const [path, options] = vi.mocked(authenticatedFetch).mock.calls[1];
  expect(path).toBe("/v1/sessions");
  expect(JSON.parse(options?.body as string)).toEqual({
    agent_id: "registered-iris",
    host_id: "approved-host",
    workspace: "/approved",
  });
});

it("opens native chat through the same tenant-bound create", async () => {
  routing.search = new URLSearchParams("mode=chat");
  vi.mocked(authenticatedFetch)
    .mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          agent_id: "registered-iris",
          bindings: [
            {
              tenant_id: "fixture",
              host_id: "approved-host",
              workspace: "/approved",
              fixture: true,
            },
          ],
        }),
      ),
    )
    .mockResolvedValueOnce(new Response(JSON.stringify({ id: "native-session" }), { status: 201 }));
  show();
  await screen.findByRole("button", { name: "Start chat" });
  fireEvent.change(screen.getByLabelText("Tenant"), { target: { value: "fixture" } });
  fireEvent.click(screen.getByRole("button", { name: "Start chat" }));
  await waitFor(() => expect(routing.navigate).toHaveBeenCalledWith("/c/native-session"));
});

it("mounts the accepted UI at the authenticated session route, in the shell's appearance", () => {
  routing.params = { sessionId: "owned-session" };
  vi.mocked(authenticatedFetch).mockResolvedValue(new Response("{}"));
  show();
  // The packaged workspace treats "system" as the OS preference, which inside
  // a drawer is the wrong system: the shell is. Carrying the resolved mode in
  // the mount URL is what makes the two agree on first paint.
  expect(screen.getByTitle("Iris workspace")).toHaveAttribute(
    "src",
    "/v1/iris/sessions/owned-session/ui/?theme=dark",
  );
});

it("tells the mounted workspace about a theme change instead of reloading it", () => {
  routing.params = { sessionId: "owned-session" };
  vi.mocked(authenticatedFetch).mockResolvedValue(new Response("{}"));
  const { rerender } = render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <IrisWorkspace />
    </QueryClientProvider>,
  );
  const frame = screen.getByTitle("Iris workspace") as HTMLIFrameElement;
  const posted: unknown[] = [];
  Object.defineProperty(frame, "contentWindow", {
    value: { postMessage: (message: unknown) => posted.push(message) },
    configurable: true,
  });
  theme.mode = "light";
  rerender(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <IrisWorkspace />
    </QueryClientProvider>,
  );
  expect(posted).toContainEqual({ irisHostTheme: "light" });
  // Reloading the iframe would discard the conversation inside it, which is
  // the one thing on this page that cannot be recovered.
  expect(frame).toHaveAttribute("src", "/v1/iris/sessions/owned-session/ui/?theme=dark");
});

it("reports missing authorization without offering a synthetic connection", async () => {
  vi.mocked(authenticatedFetch).mockResolvedValue(
    new Response(JSON.stringify({ agent_id: null, bindings: [] })),
  );
  show();
  expect(await screen.findByRole("alert")).toHaveTextContent("no authorized host binding");
  expect(screen.queryByRole("button", { name: "Open workspace" })).not.toBeInTheDocument();
});
