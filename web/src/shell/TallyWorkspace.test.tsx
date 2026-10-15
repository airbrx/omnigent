import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { getOmnigentHostConfig } from "@/lib/host";
import { authenticatedFetch } from "@/lib/identity";
import { type TallyBinding, TallyWorkspace } from "./TallyWorkspace";

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
const health = vi.hoisted(() => ({ hostOnline: undefined as boolean | null | undefined }));
vi.mock("@/hooks/RunnerHealthProvider", () => ({
  useSessionHostOnline: () => health.hostOnline,
}));
const theme = vi.hoisted(() => ({ mode: "light" as "light" | "dark" }));
vi.mock("@/components/theme/useResolvedThemeMode", () => ({
  useResolvedThemeMode: () => theme.mode,
}));

beforeEach(() => {
  vi.clearAllMocks();
  routing.params = {};
  routing.search = new URLSearchParams();
  theme.mode = "light";
  health.hostOnline = undefined;
  vi.mocked(getOmnigentHostConfig).mockReturnValue({} as never);
});

function show() {
  return render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <TallyWorkspace />
    </QueryClientProvider>,
  );
}

const BINDING: TallyBinding = {
  label: "live",
  host_id: "",
  base_url: "http://127.0.0.1:4318",
  mcp_url: "http://127.0.0.1:4318/mcp",
  host_local: false,
  fixture: false,
  workspace: null,
};
const HOSTED: TallyBinding = { ...BINDING, host_id: "coordinator-host", workspace: "/srv/tally" };

function catalog(bindings = [BINDING]) {
  vi.mocked(authenticatedFetch).mockImplementation(async (url, init) => {
    if (url === "/v1/tally")
      return new Response(JSON.stringify({ agent_id: "agent-tally", bindings }));
    if (url === "/v1/sessions" && init?.method === "POST")
      return new Response(JSON.stringify({ id: "new-session" }), { status: 201 });
    return new Response("{}", { status: 404 });
  });
}

it("frames the per-session app with the host's theme", () => {
  routing.params = { sessionId: "owned-session" };
  theme.mode = "dark";
  show();
  expect(screen.getByTitle("Tally workspace")).toHaveAttribute(
    "src",
    "/v1/tally/sessions/owned-session/ui/?theme=dark",
  );
});

it("starts a hostless session for a hostless binding, then opens the workspace", async () => {
  catalog();
  show();
  fireEvent.click(await screen.findByRole("button", { name: "Start" }));
  await waitFor(() => expect(routing.navigate).toHaveBeenCalledWith("/tally/new-session"));
  const post = vi.mocked(authenticatedFetch).mock.calls.find(([url]) => url === "/v1/sessions");
  expect(JSON.parse(String(post?.[1]?.body))).toEqual({ agent_id: "agent-tally" });
});

it("starts a session on the bound host and workspace when the binding names one", async () => {
  catalog([HOSTED]);
  show();
  fireEvent.click(await screen.findByRole("button", { name: "Start" }));
  await waitFor(() => expect(routing.navigate).toHaveBeenCalledWith("/tally/new-session"));
  const post = vi.mocked(authenticatedFetch).mock.calls.find(([url]) => url === "/v1/sessions");
  expect(JSON.parse(String(post?.[1]?.body))).toEqual({
    agent_id: "agent-tally",
    host_id: "coordinator-host",
    workspace: "/srv/tally",
  });
});

it("posts the host theme to the framed app under Tally's own key", () => {
  routing.params = { sessionId: "owned-session" };
  const post = vi.fn();
  show();
  const frame = screen.getByTitle("Tally workspace") as HTMLIFrameElement;
  Object.defineProperty(frame, "contentWindow", { value: { postMessage: post } });
  fireEvent.load(frame);
  expect(post).toHaveBeenCalledWith({ tallyHostTheme: "light" }, window.location.origin);
});

function withRecent(sessions: unknown[]) {
  vi.mocked(authenticatedFetch).mockImplementation(async (url, init) => {
    if (url === "/v1/tally")
      return new Response(JSON.stringify({ agent_id: "agent-tally", bindings: [BINDING] }));
    if (String(url).startsWith("/v1/sessions?"))
      return new Response(JSON.stringify({ data: sessions }));
    if (url === "/v1/sessions" && init?.method === "POST")
      return new Response(JSON.stringify({ id: "new-session" }), { status: 201 });
    return new Response("{}", { status: 404 });
  });
}

it("offers to resume Tally's most recent session, with New beside it", async () => {
  withRecent([{ id: "recent", host_id: null, updated_at: 1790000000 }]);
  show();
  fireEvent.click(await screen.findByRole("button", { name: "Resume" }));
  expect(routing.navigate).toHaveBeenCalledWith("/tally/recent");
  const list = vi
    .mocked(authenticatedFetch)
    .mock.calls.find(([url]) => String(url).startsWith("/v1/sessions?"));
  const params = new URLSearchParams(String(list?.[0]).split("?")[1]);
  expect(params.get("agent_id")).toBe("agent-tally");
  expect(params.get("sort_by")).toBe("updated_at");
  expect(screen.getByText(/Last used/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Start" })).toBeNull();
});

it("New still starts a fresh session when there is one to resume", async () => {
  withRecent([{ id: "recent", host_id: BINDING.host_id, updated_at: 1790000000 }]);
  show();
  fireEvent.click(await screen.findByRole("button", { name: "New" }));
  await waitFor(() => expect(routing.navigate).toHaveBeenCalledWith("/tally/new-session"));
});

it("chat mode lands in the native chat instead", async () => {
  routing.search = new URLSearchParams("mode=chat");
  catalog();
  show();
  fireEvent.click(await screen.findByRole("button", { name: "Start chat" }));
  await waitFor(() => expect(routing.navigate).toHaveBeenCalledWith("/c/new-session"));
});

it("a host binding without a workspace directory says so instead of failing on create", async () => {
  catalog([{ ...HOSTED, workspace: null }]);
  show();
  fireEvent.click(await screen.findByRole("button", { name: "Start" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("no workspace directory");
  expect(vi.mocked(authenticatedFetch).mock.calls.some(([url]) => url === "/v1/sessions")).toBe(
    false,
  );
});

it("shows the binding by name, branded, and never its loopback URL", async () => {
  catalog();
  show();
  const start = await screen.findByRole("button", { name: "Start" });
  expect(screen.getByText("Live")).toBeInTheDocument();
  expect(screen.queryByText(/127\.0\.0\.1/)).toBeNull();
  expect(screen.getByAltText("")).toHaveAttribute("src", "/v1/tally/portrait");
  expect(start.className).toContain("bg-[#FD6C1D]");
});

it("no binding for the caller is an explicit message, not an empty page", async () => {
  catalog([]);
  show();
  expect(await screen.findByRole("alert")).toHaveTextContent("no binding for you");
});

it("an embedded host offers the native chat instead of a frame it cannot route", () => {
  routing.params = { sessionId: "owned-session" };
  vi.mocked(getOmnigentHostConfig).mockReturnValue({ fetcher: vi.fn() } as never);
  show();
  expect(screen.queryByTitle("Tally workspace")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Open native chat instead" }));
  expect(routing.navigate).toHaveBeenCalledWith("/c/owned-session");
});

it("keeps the frame below the shell header, so the header cannot take the tab bar's clicks", () => {
  // AppShell lays ChatHeader over <main> (absolute, top-0, z-30, h-14 / md:h-12,
  // transparent), and Tally's tab bar and Refresh are the top of the frame.
  routing.params = { sessionId: "owned-session" };
  const { container } = show();
  const frame = screen.getByTitle("Tally workspace");
  const clearance = container.querySelector("[data-tally-header-clearance]");
  expect(clearance).not.toBeNull();
  expect(clearance).toHaveAttribute("aria-hidden", "true");
  expect(clearance).toHaveClass("h-14", "md:h-12", "shrink-0");
  expect(clearance!.compareDocumentPosition(frame) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
});

function hostStatus(status: "online" | "offline", post = 201) {
  vi.mocked(authenticatedFetch).mockImplementation(async (url, init) => {
    if (url === "/v1/tally")
      return new Response(JSON.stringify({ agent_id: "agent-tally", bindings: [HOSTED] }));
    if (url === "/v1/hosts")
      return new Response(
        JSON.stringify({
          hosts: [{ host_id: "coordinator-host", name: "mac", owner: "abram", status }],
        }),
      );
    if (url === "/v1/sessions" && init?.method === "POST")
      return new Response(JSON.stringify({ id: "new-session", detail: "host offline" }), {
        status: post,
      });
    return new Response("{}", { status: 404 });
  });
}

function expectPortalTabs() {
  for (const name of ["Analytics", "Agent management", "Sprint board"])
    expect(screen.getByRole("tab", { name })).toBeInTheDocument();
}

it("an offline host shows Agent not connected, with the portal tabs still usable", async () => {
  hostStatus("offline");
  const { container } = show();
  const panel = await screen.findByRole("status", { name: "Agent not connected" });
  expect(panel).toHaveTextContent("Agent not connected");
  expect(panel).toHaveTextContent("chat is unavailable");
  expect(screen.queryByRole("button", { name: "Start" })).toBeNull();
  expect(container.querySelector("[data-tally-header-clearance]")).not.toBeNull();
  expectPortalTabs();
  expect(screen.getByTitle("Portal: Analytics")).toHaveAttribute("src", "/gateway/app/#overview");
  fireEvent.click(screen.getByRole("tab", { name: "Sprint board" }));
  expect(screen.getByTitle("Portal: Sprint board")).toHaveAttribute("src", "/gateway/app/#board");
});

it("Retry rechecks the host and returns to Start once it is back online", async () => {
  hostStatus("offline");
  show();
  await screen.findByRole("status", { name: "Agent not connected" });
  hostStatus("online");
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  expect(await screen.findByRole("button", { name: "Start" })).toBeInTheDocument();
  expect(screen.queryByRole("status", { name: "Agent not connected" })).toBeNull();
});

it("a start the offline host refuses shows Agent not connected, not a generic failure", async () => {
  hostStatus("online", 409);
  show();
  fireEvent.click(await screen.findByRole("button", { name: "Start" }));
  expect(await screen.findByRole("status", { name: "Agent not connected" })).toBeInTheDocument();
  expect(screen.queryByText(/Could not start Tally/)).toBeNull();
  expectPortalTabs();
  expect(routing.navigate).not.toHaveBeenCalled();
});

it("a hostless start failure keeps the plain error", async () => {
  vi.mocked(authenticatedFetch).mockImplementation(async (url, init) => {
    if (url === "/v1/tally")
      return new Response(JSON.stringify({ agent_id: "agent-tally", bindings: [BINDING] }));
    if (url === "/v1/sessions" && init?.method === "POST")
      return new Response("{}", { status: 500 });
    return new Response("{}", { status: 404 });
  });
  show();
  fireEvent.click(await screen.findByRole("button", { name: "Start" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Could not start Tally");
  expect(screen.queryByRole("status", { name: "Agent not connected" })).toBeNull();
});

it("an open session whose host goes offline keeps its frame and says so", () => {
  routing.params = { sessionId: "owned-session" };
  health.hostOnline = false;
  show();
  expect(screen.getByRole("status", { name: "Agent not connected" })).toBeInTheDocument();
  expect(screen.getByTitle("Tally workspace")).toBeInTheDocument();
});
