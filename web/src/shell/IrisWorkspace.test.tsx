import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { getOmnigentHostConfig } from "@/lib/host";
import { authenticatedFetch } from "@/lib/identity";
import { IrisWorkspace, shortId } from "./IrisWorkspace";

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
vi.mock("@/lib/host", () => ({ getOmnigentHostConfig: vi.fn(() => ({})) }));
const theme = vi.hoisted(() => ({ mode: "dark" as "light" | "dark" }));
vi.mock("@/components/theme/useResolvedThemeMode", () => ({
  useResolvedThemeMode: () => theme.mode,
}));

beforeEach(() => {
  vi.clearAllMocks();
  routing.params = {};
  routing.search = new URLSearchParams();
  theme.mode = "dark";
  vi.mocked(getOmnigentHostConfig).mockReturnValue({} as never);
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

it("refuses to frame the workspace in an embedded host, and offers the same session's chat", () => {
  // An <iframe src> cannot be routed through the host's `fetcher`, so the page
  // would resolve against the wrong origin and render an empty rectangle. An
  // empty rectangle is a worse answer than "not available here".
  routing.params = { sessionId: "owned-session" };
  vi.mocked(getOmnigentHostConfig).mockReturnValue({ fetcher: vi.fn() } as never);
  vi.mocked(authenticatedFetch).mockResolvedValue(new Response("{}"));
  show();
  expect(screen.queryByTitle("Iris workspace")).not.toBeInTheDocument();
  expect(screen.getByRole("alert")).toHaveTextContent("a framed page cannot use");
  fireEvent.click(screen.getByRole("button", { name: "Open native chat instead" }));
  expect(routing.navigate).toHaveBeenCalledWith("/c/owned-session");
});

// Regression: `fixture-iris` rendered as `fixture-`, a fragment that identifies
// nothing and reads as a rendering bug.
it("shows a short tenant id whole rather than cutting it to a fragment", () => {
  expect(shortId("fixture-iris")).toBe("fixture-iris");
});

it("truncates a UUID, which costs 36 characters to say nothing", () => {
  expect(shortId("f65d9135-0ba3-4c58-8768-c48a1334041d")).toBe("f65d9135");
});

it("keeps enough of a UUID to tell two tenants apart", () => {
  expect(shortId("f65d9135-0ba3-4c58-8768-c48a1334041d")).not.toBe(
    shortId("f65d9136-0ba3-4c58-8768-c48a1334041d"),
  );
});
