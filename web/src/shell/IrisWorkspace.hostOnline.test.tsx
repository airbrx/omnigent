import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { authenticatedFetch } from "@/lib/identity";
import { IrisWorkspace } from "./IrisWorkspace";

// The account table already disables a row whose host is offline, and React
// swallows clicks on a disabled button — so through the real table, create()
// can never be reached for a `false` binding and its own refusal is invisible.
// That refusal is the last stop before POST /v1/sessions, and it has to hold
// for any caller that lets the click through: a stale row, or a future view
// that never heard of host_online. This file stands the table in with a plain
// list that always offers the button, so the guard is the only thing on trial.

const routing = vi.hoisted(() => ({ navigate: vi.fn() }));
vi.mock("@/lib/routing", () => ({
  useNavigate: () => routing.navigate,
  useParams: () => ({}),
  useSearchParams: () => [new URLSearchParams()],
}));
vi.mock("@/lib/identity", () => ({ authenticatedFetch: vi.fn() }));
vi.mock("@/lib/host", () => ({ getOmnigentHostConfig: () => ({}) }));
vi.mock("@/components/theme/useResolvedThemeMode", () => ({ useResolvedThemeMode: () => "dark" }));
vi.mock("./IrisAccountView", () => ({
  shortId: (id: string) => id,
  IrisAccountView: ({
    tenants,
    openLabel,
    onOpen,
  }: {
    tenants: { tenant_id: string }[];
    openLabel: string;
    onOpen: (tenantId: string) => void;
  }) => (
    <ul>
      {tenants.map((t) => (
        <li key={t.tenant_id}>
          <button type="button" onClick={() => onOpen(t.tenant_id)}>
            {openLabel}: {t.tenant_id}
          </button>
        </li>
      ))}
    </ul>
  ),
}));

beforeEach(() => {
  vi.clearAllMocks();
});

function show(hostOnline: boolean | null) {
  vi.mocked(authenticatedFetch).mockImplementation(async (input, init) => {
    const url = String(input);
    if (url === "/v1/iris")
      return new Response(
        JSON.stringify({
          agent_id: "registered-iris",
          bindings: [
            {
              tenant_id: "live-tenant",
              host_id: "mac-mini",
              workspace: "/live",
              fixture: false,
              host_online: hostOnline,
            },
          ],
        }),
      );
    if (url === "/v1/iris/account")
      return new Response(
        JSON.stringify({ generated_at: 1, tenants: 1, ranked: [], quarantined: [] }),
      );
    if (url === "/v1/sessions" && init?.method === "POST")
      return new Response(JSON.stringify({ id: "native-session" }), { status: 201 });
    throw new Error(`unexpected fetch ${url}`);
  });
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <IrisWorkspace />
    </QueryClientProvider>,
  );
}

const posts = () =>
  vi.mocked(authenticatedFetch).mock.calls.filter(([, init]) => init?.method === "POST");

it("refuses to create a session on a host the catalog reports offline, naming the host", async () => {
  show(false);
  fireEvent.click(await screen.findByRole("button", { name: "Open workspace: live-tenant" }));
  expect(await screen.findByRole("alert")).toHaveTextContent('Host "mac-mini" is offline');
  expect(posts()).toHaveLength(0);
  expect(routing.navigate).not.toHaveBeenCalled();
});

it("creates the session when host liveness is unknown: null is not offline", async () => {
  show(null);
  fireEvent.click(await screen.findByRole("button", { name: "Open workspace: live-tenant" }));
  await waitFor(() => expect(routing.navigate).toHaveBeenCalledWith("/iris/native-session"));
  expect(posts()).toHaveLength(1);
  expect(JSON.parse(posts()[0][1]?.body as string)).toEqual({
    agent_id: "registered-iris",
    host_id: "mac-mini",
    workspace: "/live",
  });
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

it("creates the session when the host is online", async () => {
  show(true);
  fireEvent.click(await screen.findByRole("button", { name: "Open workspace: live-tenant" }));
  await waitFor(() => expect(routing.navigate).toHaveBeenCalledWith("/iris/native-session"));
  expect(posts()).toHaveLength(1);
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});
