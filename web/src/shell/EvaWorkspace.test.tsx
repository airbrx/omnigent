import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { getOmnigentHostConfig } from "@/lib/host";
import { authenticatedFetch } from "@/lib/identity";
import { EvaWorkspace } from "./EvaWorkspace";

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
const theme = vi.hoisted(() => ({ mode: "light" as "light" | "dark" }));
vi.mock("@/components/theme/useResolvedThemeMode", () => ({
  useResolvedThemeMode: () => theme.mode,
}));

beforeEach(() => {
  vi.clearAllMocks();
  routing.params = {};
  routing.search = new URLSearchParams();
  theme.mode = "light";
  vi.mocked(getOmnigentHostConfig).mockReturnValue({} as never);
});

function show() {
  return render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <EvaWorkspace />
    </QueryClientProvider>,
  );
}

const BINDING = {
  label: "live",
  host_id: "eva-host",
  base_url: "http://127.0.0.1:8000",
  mcp_url: "http://127.0.0.1:8000/mcp/",
  host_local: true,
  fixture: false,
  workspace: "/Users/me/eva",
};

function catalog(bindings = [BINDING]) {
  vi.mocked(authenticatedFetch).mockImplementation(async (url, init) => {
    if (url === "/v1/eva") return new Response(JSON.stringify({ agent_id: "agent-eva", bindings }));
    if (url === "/v1/sessions" && init?.method === "POST")
      return new Response(JSON.stringify({ id: "new-session" }), { status: 201 });
    return new Response("{}", { status: 404 });
  });
}

it("frames the per-session app with the host's theme", () => {
  routing.params = { sessionId: "owned-session" };
  theme.mode = "dark";
  show();
  expect(screen.getByTitle("Eva workspace")).toHaveAttribute(
    "src",
    "/v1/eva/sessions/owned-session/ui/?theme=dark",
  );
});

it("starts a session on the bound host and workspace, then opens the workspace", async () => {
  catalog();
  show();
  fireEvent.click(await screen.findByRole("button", { name: "Open workspace" }));
  await waitFor(() => expect(routing.navigate).toHaveBeenCalledWith("/eva/new-session"));
  const post = vi.mocked(authenticatedFetch).mock.calls.find(([url]) => url === "/v1/sessions");
  expect(JSON.parse(String(post?.[1]?.body))).toEqual({
    agent_id: "agent-eva",
    host_id: "eva-host",
    workspace: "/Users/me/eva",
  });
});

it("chat mode lands in the native chat instead", async () => {
  routing.search = new URLSearchParams("mode=chat");
  catalog();
  show();
  fireEvent.click(await screen.findByRole("button", { name: "Start chat" }));
  await waitFor(() => expect(routing.navigate).toHaveBeenCalledWith("/c/new-session"));
});

it("a host binding without a workspace directory says so instead of failing on create", async () => {
  catalog([{ ...BINDING, workspace: null }]);
  show();
  fireEvent.click(await screen.findByRole("button", { name: "Open workspace" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("no workspace directory");
  expect(vi.mocked(authenticatedFetch).mock.calls.some(([url]) => url === "/v1/sessions")).toBe(
    false,
  );
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
  expect(screen.queryByTitle("Eva workspace")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Open native chat instead" }));
  expect(routing.navigate).toHaveBeenCalledWith("/c/owned-session");
});
