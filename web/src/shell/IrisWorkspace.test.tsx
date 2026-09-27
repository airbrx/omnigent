import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { getOmnigentHostConfig } from "@/lib/host";
import { authenticatedFetch } from "@/lib/identity";
import { IrisWorkspace, resumableSession, shortId } from "./IrisWorkspace";

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
    // One host the server can see and one it cannot: `true` and `null` are
    // both openable, so every existing drill-in test exercises that contract.
    {
      tenant_id: "fixture",
      host_id: "approved-host",
      workspace: "/approved",
      fixture: true,
      host_online: true,
    },
    {
      tenant_id: "live-tenant",
      host_id: "quiet-host",
      workspace: "/live",
      fixture: false,
      host_online: null,
    },
  ],
};
const ACCOUNT = {
  generated_at: 1,
  tenants: 2,
  ranked: [
    {
      tenant_id: "live-tenant",
      name: null,
      hit_rate: 0.5,
      hit_rate_denominator: 10,
      requests: 10,
      cache_misses: 5,
      covered_days: 7,
      requested_days: 7,
      captured_at: 0,
      age_seconds: 120,
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
  expect(
    screen
      .getAllByRole("row")
      .slice(1)
      .map((r) => r.getAttribute("data-tenant")),
  ).toEqual(["live-tenant", "fixture"]);
  fireEvent.click(within(rowFor("fixture")).getByRole("button", { name: /^Open workspace/ }));
  await waitFor(() => expect(routing.navigate).toHaveBeenCalledWith("/iris/native-session"));
  const create = vi
    .mocked(authenticatedFetch)
    .mock.calls.find(([, init]) => init?.method === "POST");
  expect(create?.[0]).toBe("/v1/sessions");
  expect(JSON.parse(create?.[1]?.body as string)).toEqual({
    agent_id: "registered-iris",
    host_id: "approved-host",
    workspace: "/approved",
  });
  // No model ran to build the list: the only POST is the drill-in.
  expect(
    vi.mocked(authenticatedFetch).mock.calls.filter(([, init]) => init?.method === "POST"),
  ).toHaveLength(1);
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
      new Response(
        JSON.stringify({
          detail: "The session list could not be read, so the account cannot be shown",
        }),
        {
          status: 502,
        },
      ),
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

it("will not offer a tenant whose host the catalog reports offline, and never POSTs for it", async () => {
  // Production Iris was bound only to a Mac mini that had gone to sleep: every
  // tenant looked openable and every session creation failed with a 400.
  serve({
    "/v1/iris": () =>
      new Response(
        JSON.stringify({
          ...CATALOG,
          bindings: CATALOG.bindings.map((b) =>
            b.tenant_id === "fixture" ? { ...b, host_online: false } : b,
          ),
        }),
      ),
  });
  show();
  await screen.findByText("Never collected");
  const down = rowFor("fixture");
  expect(down).toHaveTextContent("host offline");
  const button = within(down).getByRole("button", { name: /^Open workspace/ });
  expect(button).toBeDisabled();
  fireEvent.click(button);
  expect(
    vi.mocked(authenticatedFetch).mock.calls.filter(([, init]) => init?.method === "POST"),
  ).toHaveLength(0);
  expect(routing.navigate).not.toHaveBeenCalled();
  // The other binding is unaffected: one sleeping host is not an outage.
  expect(
    within(rowFor("live-tenant")).getByRole("button", { name: /^Open workspace/ }),
  ).not.toBeDisabled();
});

it("offers a tenant whose host is online, with the same create request as before", async () => {
  serve();
  show();
  await screen.findByText("Never collected");
  const up = rowFor("fixture");
  expect(up).not.toHaveTextContent(/offline/);
  fireEvent.click(within(up).getByRole("button", { name: /^Open workspace/ }));
  await waitFor(() => expect(routing.navigate).toHaveBeenCalledWith("/iris/native-session"));
  const create = vi
    .mocked(authenticatedFetch)
    .mock.calls.find(([, init]) => init?.method === "POST");
  // host_online is a catalog fact, not a session parameter: the body is unchanged.
  expect(JSON.parse(create?.[1]?.body as string)).toEqual({
    agent_id: "registered-iris",
    host_id: "approved-host",
    workspace: "/approved",
  });
});

it("treats unknown host liveness as openable and says nothing about it", async () => {
  // `null`: the server could not tell. That is not offline, and greying the
  // row out would manufacture an outage every time the lookup went missing.
  serve();
  show();
  await screen.findByText("Never collected");
  const unknown = rowFor("live-tenant");
  expect(unknown).not.toHaveTextContent(/offline/);
  expect(unknown).not.toHaveTextContent(/unknown/);
  fireEvent.click(within(unknown).getByRole("button", { name: /^Open workspace/ }));
  await waitFor(() => expect(routing.navigate).toHaveBeenCalledWith("/iris/native-session"));
  const create = vi
    .mocked(authenticatedFetch)
    .mock.calls.find(([, init]) => init?.method === "POST");
  expect(JSON.parse(create?.[1]?.body as string)).toEqual({
    agent_id: "registered-iris",
    host_id: "quiet-host",
    workspace: "/live",
  });
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
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

// ---------------------------------------------------------------------------
// Workspace v2 (docs/iris/WORKSPACE_V2.md): branded landing, Resume per
// tenant, and the open-tenant handoff from the framed app.
// ---------------------------------------------------------------------------

function posts() {
  return vi.mocked(authenticatedFetch).mock.calls.filter(([, init]) => init?.method === "POST");
}

it("brands the landing as Iris's, with her packaged portrait and the triage table as the picker", async () => {
  serve();
  show();
  await screen.findByText("Never collected");
  // Her portrait comes from the pinned archive through the existing route:
  // no image is committed into the web bundle.
  const portrait = document.querySelector("img");
  expect(portrait).toHaveAttribute("src", "/v1/iris/portrait");
  expect(screen.getByText("airbrx cache intelligence")).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Iris workspace" })).toBeInTheDocument();
  expect(document.querySelector('link[href*="family=Inter"]')).not.toBeNull();
  // Multi-tenant: the account table is still how a tenant is chosen.
  expect(screen.getByRole("table", { name: "Tenants in this account" })).toBeInTheDocument();
  expect(screen.getByText(/Monitoring is unavailable/)).toBeInTheDocument();
});

// Both production tenants share one execution host. A match on the host alone
// resumes whichever tenant was used last, which is the wrong tenant half the time.
const SHARED = {
  agent_id: "registered-iris",
  bindings: [
    {
      tenant_id: "tenant-a",
      host_id: "shared-host",
      workspace: "/a",
      fixture: false,
      host_online: true,
    },
    {
      tenant_id: "tenant-b",
      host_id: "shared-host",
      workspace: "/b",
      fixture: true,
      host_online: true,
    },
    {
      tenant_id: "tenant-c",
      host_id: "shared-host",
      workspace: "/c",
      fixture: false,
      host_online: true,
    },
  ],
};
const RECENT = [
  // Newest first, as the query asks for them.
  { id: "session-b", host_id: "shared-host", workspace: "/b", updated_at: 1_790_000_300 },
  { id: "session-elsewhere", host_id: "other-host", workspace: "/a", updated_at: 1_790_000_200 },
  { id: "session-a", host_id: "shared-host", workspace: "/a", updated_at: 1_790_000_100 },
];

function serveShared() {
  serve({
    "/v1/iris": () => new Response(JSON.stringify(SHARED)),
    "/v1/iris/account": () =>
      new Response(JSON.stringify({ generated_at: 1, tenants: 3, ranked: [], quarantined: [] })),
  });
  const base = vi.mocked(authenticatedFetch).getMockImplementation();
  vi.mocked(authenticatedFetch).mockImplementation(async (input, init) => {
    if (String(input).startsWith("/v1/sessions?"))
      return new Response(JSON.stringify({ data: RECENT }));
    return base!(input, init);
  });
}

it("matches a session to a tenant on host AND workspace, never the host alone", () => {
  expect(resumableSession(SHARED.bindings[0], RECENT)?.id).toBe("session-a");
  expect(resumableSession(SHARED.bindings[1], RECENT)?.id).toBe("session-b");
  expect(resumableSession(SHARED.bindings[2], RECENT)).toBeUndefined();
});

it("resumes each tenant's own last session by navigating, with no POST", async () => {
  serveShared();
  show();
  const resumeA = await screen.findByRole("button", { name: "Resume: tenant-a" });
  // The recent-sessions read is the caller's own Iris sessions, newest first.
  const read = vi
    .mocked(authenticatedFetch)
    .mock.calls.find(([url]) => String(url).startsWith("/v1/sessions?"));
  const params = new URLSearchParams(String(read?.[0]).split("?")[1]);
  expect(params.get("agent_id")).toBe("registered-iris");
  expect(params.get("sort_by")).toBe("updated_at");
  expect(params.get("visibility")).toBe("mine");
  fireEvent.click(resumeA);
  expect(routing.navigate).toHaveBeenCalledWith("/iris/session-a");
  expect(posts()).toHaveLength(0);
  // tenant-c shares the host but has no session in its workspace: New only.
  const rowC = rowFor("tenant-c");
  expect(within(rowC).queryByRole("button", { name: /^Resume/ })).not.toBeInTheDocument();
  expect(within(rowC).getByRole("button", { name: "Open workspace: tenant-c" })).toBeEnabled();
  // New beside Resume is still the tenant-bound create().
  fireEvent.click(within(rowFor("tenant-b")).getByRole("button", { name: "New: tenant-b" }));
  await waitFor(() => expect(routing.navigate).toHaveBeenCalledWith("/iris/native-session"));
  expect(posts()).toHaveLength(1);
  expect(JSON.parse(posts()[0][1]?.body as string)).toEqual({
    agent_id: "registered-iris",
    host_id: "shared-host",
    workspace: "/b",
  });
});

it("does not offer Resume or New for a tenant whose host is offline", async () => {
  serveShared();
  const base = vi.mocked(authenticatedFetch).getMockImplementation();
  vi.mocked(authenticatedFetch).mockImplementation(async (input, init) => {
    if (String(input) === "/v1/iris")
      return new Response(
        JSON.stringify({
          ...SHARED,
          bindings: SHARED.bindings.map((b) =>
            b.tenant_id === "tenant-a" ? { ...b, host_online: false } : b,
          ),
        }),
      );
    return base!(input, init);
  });
  show();
  const resumeA = await screen.findByRole("button", { name: "Resume: tenant-a" });
  expect(resumeA).toBeDisabled();
  expect(within(rowFor("tenant-a")).getByRole("button", { name: "New: tenant-a" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Resume: tenant-b" })).toBeEnabled();
});

describe("the iris.openTenant handoff from the framed workspace", () => {
  async function mounted(overrides: Record<string, () => Response> = {}) {
    routing.params = { sessionId: "owned-session" };
    serve(overrides);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <IrisWorkspace />
      </QueryClientProvider>,
    );
    // The listener checks the tenant against the catalog, so wait for it.
    await waitFor(() => expect(client.getQueryData(["iris-workspace"])).toBeDefined());
    return screen.getByTitle("Iris workspace") as HTMLIFrameElement;
  }
  function post(data: unknown, init: { origin?: string; source?: MessageEventSource | null } = {}) {
    const frame = screen.getByTitle("Iris workspace") as HTMLIFrameElement;
    act(() => {
      window.dispatchEvent(
        new MessageEvent("message", {
          data,
          origin: init.origin ?? window.location.origin,
          source: init.source === undefined ? frame.contentWindow : init.source,
        }),
      );
    });
  }
  const open = (tenant_id: unknown) => ({ type: "iris.openTenant", tenant_id });

  it("opens a NEW session for a bound tenant through the existing create()", async () => {
    const frame = await mounted();
    expect(frame.contentWindow).not.toBeNull();
    post(open("live-tenant"));
    await waitFor(() => expect(routing.navigate).toHaveBeenCalledWith("/iris/native-session"));
    expect(posts()).toHaveLength(1);
    expect(posts()[0][0]).toBe("/v1/sessions");
    expect(JSON.parse(posts()[0][1]?.body as string)).toEqual({
      agent_id: "registered-iris",
      host_id: "quiet-host",
      workspace: "/live",
    });
  });

  it("ignores a message from a foreign origin", async () => {
    await mounted();
    post(open("live-tenant"), { origin: "https://evil.example" });
    await act(async () => {});
    expect(posts()).toHaveLength(0);
    // The listener was live: the same message from the right origin opens.
    post(open("live-tenant"));
    await waitFor(() => expect(posts()).toHaveLength(1));
  });

  it("ignores a same-origin message that did not come from the workspace frame", async () => {
    await mounted();
    post(open("live-tenant"), { source: window });
    await act(async () => {});
    expect(posts()).toHaveLength(0);
    post(open("live-tenant"));
    await waitFor(() => expect(posts()).toHaveLength(1));
  });

  it("ignores a tenant the caller is not bound to, and anything that is not a tenant id", async () => {
    await mounted();
    post(open("someone-elses-tenant"));
    post(open(42));
    post(open(null));
    post({ type: "iris.somethingElse", tenant_id: "live-tenant" });
    post("iris.openTenant");
    await act(async () => {});
    expect(posts()).toHaveLength(0);
    expect(routing.navigate).not.toHaveBeenCalled();
    post(open("fixture"));
    await waitFor(() => expect(posts()).toHaveLength(1));
    expect(JSON.parse(posts()[0][1]?.body as string).workspace).toBe("/approved");
  });

  it("opens one session for a burst of messages, and takes the next one after it settles", async () => {
    // Review of #108: five quick messages created five sessions.
    let finish: (response: Response) => void = () => {};
    await mounted({
      "/v1/sessions": () =>
        new Promise<Response>((resolve) => {
          finish = resolve;
        }) as unknown as Response,
    });
    for (let i = 0; i < 5; i++) post(open("live-tenant"));
    post(open("fixture"));
    await act(async () => {});
    expect(posts()).toHaveLength(1);
    await act(async () => {
      finish(new Response(JSON.stringify({ id: "native-session" }), { status: 201 }));
    });
    await waitFor(() => expect(routing.navigate).toHaveBeenCalledTimes(1));
    expect(routing.navigate).toHaveBeenCalledWith("/iris/native-session");
    // The guard is released once create() settles, so it is not a one-shot.
    post(open("fixture"));
    await waitFor(() => expect(posts()).toHaveLength(2));
  });

  it("says so on the session page when opening the tenant fails, without reloading the workspace", async () => {
    // Review of #108: create() set the error, but only the landing rendered it.
    const frame = await mounted({
      "/v1/sessions": () =>
        new Response(JSON.stringify({ detail: "host said no" }), { status: 500 }),
    });
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    post(open("live-tenant"));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(
      "Iris could not open that tenant in a new session. Could not start Iris. Check that the selected host is online.",
    );
    // Host error text is never shown.
    expect(alert).not.toHaveTextContent("host said no");
    expect(routing.navigate).not.toHaveBeenCalled();
    // Same iframe element: showing the error did not remount (and reload) it.
    expect(screen.getByTitle("Iris workspace")).toBe(frame);
    fireEvent.click(within(alert).getByRole("button", { name: "Dismiss" }));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByTitle("Iris workspace")).toBe(frame);
    // A failed open releases the guard too.
    post(open("live-tenant"));
    await waitFor(() => expect(posts()).toHaveLength(2));
  });
});

it("with the v2 switch off, frames the pinned UI exactly as before and its messages open nothing", async () => {
  // The switch is read per request by the asset route (D1), so the shell frames
  // the same URL either way; what the pinned UI posts must not open anything.
  routing.params = { sessionId: "owned-session" };
  serve();
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <IrisWorkspace />
    </QueryClientProvider>,
  );
  await waitFor(() => expect(client.getQueryData(["iris-workspace"])).toBeDefined());
  const frame = screen.getByTitle("Iris workspace") as HTMLIFrameElement;
  expect(frame).toHaveAttribute("src", "/v1/iris/sessions/owned-session/ui/?theme=dark");
  for (const token of [
    "allow-scripts",
    "allow-same-origin",
    "allow-forms",
    "allow-downloads",
    "allow-top-navigation-by-user-activation",
  ])
    expect(frame.getAttribute("sandbox")?.split(" ")).toContain(token);
  act(() => {
    for (const data of [{ irisHostTheme: "light" }, { tenant_id: "live-tenant" }, "refresh"])
      window.dispatchEvent(
        new MessageEvent("message", {
          data,
          origin: window.location.origin,
          source: frame.contentWindow,
        }),
      );
  });
  await act(async () => {});
  expect(posts()).toHaveLength(0);
  expect(routing.navigate).not.toHaveBeenCalled();
  // No recent-sessions read inside a session: that is the landing's alone.
  expect(
    vi
      .mocked(authenticatedFetch)
      .mock.calls.some(([url]) => String(url).startsWith("/v1/sessions?")),
  ).toBe(false);
});

it("with nothing to resume, the landing offers the one Open button per tenant, as before", async () => {
  serve();
  show();
  await screen.findByText("Never collected");
  expect(screen.getAllByRole("button", { name: /^Open workspace/ })).toHaveLength(2);
  expect(screen.queryByRole("button", { name: /^Resume/ })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /^New/ })).not.toBeInTheDocument();
});
