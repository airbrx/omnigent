import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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

const CATALOG = {
  agent_id: "registered-iris",
  bindings: [
    { tenant_id: "fixture", host_id: "approved-host", workspace: "/approved", fixture: true },
    { tenant_id: "live-tenant", host_id: "approved-host", workspace: "/live", fixture: false },
  ],
};
const ACCOUNT = {
  generated_at: 1,
  tenants: 2,
  ranked: [
    {
      tenant_id: "live-tenant", name: null, hit_rate: 0.5, hit_rate_denominator: 10, requests: 10,
      cache_misses: 5, covered_days: 7, requested_days: 7, captured_at: 0, age_seconds: 120,
    },
  ],
  quarantined: [{ tenant_id: "fixture", name: null, reason: "never_collected", detail: null }],
};

/** Dispatch the fetch mock by URL so the order of queries does not matter. */
function serve(overrides: Record<string, () => Response> = {}) {
  vi.mocked(authenticatedFetch).mockImplementation(async (input, init) => {
    const url = String(input);
    if (url in overrides) return overrides[url]();
    if (url === "/v1/iris") return new Response(JSON.stringify(CATALOG));
    if (url === "/v1/iris/account") return new Response(JSON.stringify(ACCOUNT));
    if (url === "/v1/sessions" && init?.method === "POST")
      return new Response(JSON.stringify({ id: "native-session" }), { status: 201 });
    throw new Error(`unexpected fetch ${url}`);
  });
}

function rowFor(text: string) {
  const row = screen.getAllByRole("row").find((r) => r.textContent?.includes(text));
  if (!row) throw new Error(`no row containing ${text}`);
  return row;
}

it("drills into a tenant through native session creation with that tenant's binding", async () => {
  serve();
  show();
  await screen.findByText("Never collected");
  // Ranked before quarantined, as the server ordered them.
  expect(screen.getAllByRole("row").slice(1).map((r) => r.getAttribute("data-tenant"))).toEqual([
    "live-tenant",
    "fixture",
  ]);
  fireEvent.click(within(rowFor("fixture")).getByRole("button", { name: /^Open workspace/ }));
  await waitFor(() => expect(routing.navigate).toHaveBeenCalledWith("/iris/native-session"));
  const create = vi.mocked(authenticatedFetch).mock.calls.find(([, init]) => init?.method === "POST");
  expect(create?.[0]).toBe("/v1/sessions");
  expect(JSON.parse(create?.[1]?.body as string)).toEqual({
    agent_id: "registered-iris",
    host_id: "approved-host",
    workspace: "/approved",
  });
  // No model ran to build the list: the only POST is the drill-in.
  expect(vi.mocked(authenticatedFetch).mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(1);
});

it("opens native chat through the same tenant-bound create", async () => {
  routing.search = new URLSearchParams("mode=chat");
  serve();
  show();
  await screen.findByText("Never collected");
  fireEvent.click(within(rowFor("live-tenant")).getByRole("button", { name: /^Start chat/ }));
  await waitFor(() => expect(routing.navigate).toHaveBeenCalledWith("/c/native-session"));
});

it("keeps drill-in available when the account request fails, and says why", async () => {
  serve({
    "/v1/iris/account": () =>
      new Response(JSON.stringify({ detail: "The session list could not be read, so the account cannot be shown" }), {
        status: 502,
      }),
  });
  show();
  expect(await screen.findByRole("alert")).toHaveTextContent("session list could not be read");
  expect(screen.getAllByRole("button", { name: /^Open workspace/ })).toHaveLength(2);
});

it("says Ranking… while the account query is pending, without disabling drill-in", async () => {
  vi.mocked(authenticatedFetch).mockImplementation(async (input) => {
    const url = String(input);
    if (url === "/v1/iris") return new Response(JSON.stringify(CATALOG));
    if (url === "/v1/iris/account") return new Promise<Response>(() => {});
    throw new Error(`unexpected fetch ${url}`);
  });
  show();
  const statuses = await screen.findAllByText("Ranking…");
  expect(statuses).toHaveLength(2);
  for (const status of statuses) expect(status).toHaveAttribute("role", "status");
  const buttons = screen.getAllByRole("button", { name: /^Open workspace/ });
  expect(buttons).toHaveLength(2);
  for (const button of buttons) expect(button).not.toBeDisabled();
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
