// Real coverage for the agent-drawer wiring Task 6 added, replacing
// AgentDrawer.integration.test.tsx — that file defined its own
// `data-testid="browse-agents-button"` in a local harness and asserted that
// a `useState` the test itself wrote flipped, which is a tautology (it also
// duplicated cases AgentDrawer.test.tsx already covers). Nothing tested that
// the real Sidebar button calls `onBrowseAgents`, or that AppShell's own
// `onSelectAgent` handler does the right thing — which is also exactly what
// would have caught the "starts the wrong agent" regression: an earlier,
// reversed version of this handler called `writeLastAgentId` + `navigate("/")`
// and relied on NewChatDialog.tsx remounting to pick it up, which it never
// does when already on "/" (see NewChatDialog.tsx's `?agent=` effect and its
// own tests in NewChatDialog.test.tsx for the landing-side half of this).
//
// Sidebar itself is stubbed, matching AppShell.test.tsx's own convention for
// this heavy component — the real button-click-calls-the-prop wiring is
// covered directly in Sidebar.test.tsx's "Sidebar browse-agents trigger"
// case. The stub here exposes the exact same `onBrowseAgents` prop AppShell
// really passes, so a click on it exercises AppShell's real handler. Every
// other component here — <AppShell>, <AgentDrawer>, `writeLastAgentId` — is
// the genuine, unmocked implementation; only the two network-backed hooks
// AgentDrawer reads are stubbed with a fixed roster.

import type * as UseTerminalsModule from "@/hooks/useTerminals";
import type * as UseChildSessionsModule from "@/hooks/useChildSessions";
import type * as UseSessionModule from "@/hooks/useSession";
import type * as UseConversationsModule from "@/hooks/useConversations";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { TooltipProvider } from "@/components/ui/tooltip";

vi.mock("@/hooks/useConversations", async (importOriginal) => ({
  ...(await importOriginal<typeof UseConversationsModule>()),
  useConversations: vi.fn(() => ({
    data: {
      pages: [{ data: [], first_id: null, last_id: null, has_more: false }],
      pageParams: [undefined],
    },
  })),
  useProjects: vi.fn(() => ({ data: [] })),
}));
vi.mock("@/hooks/useTerminals", async (importOriginal) => ({
  ...(await importOriginal<typeof UseTerminalsModule>()),
  useTerminals: vi.fn(() => ({ terminals: [], isLoading: false, error: null })),
}));
vi.mock("@/hooks/useWorkspaceChangedFiles", () => ({
  useWorkspaceEnvironment: vi.fn(() => ({ data: undefined, isLoading: true })),
  useWorkspaceChangedFiles: vi.fn(() => ({ data: undefined, isLoading: true })),
}));
vi.mock("@/hooks/useGithub", () => ({
  useGithubInfo: vi.fn(() => ({ data: undefined, isLoading: true })),
}));
vi.mock("@/hooks/useChildSessions", async (importOriginal) => ({
  ...(await importOriginal<typeof UseChildSessionsModule>()),
  useChildSessions: vi.fn(() => ({ children: [], isLoading: false, error: null })),
}));
vi.mock("@/hooks/useSession", async (importOriginal) => ({
  ...(await importOriginal<typeof UseSessionModule>()),
  useSession: vi.fn(() => ({ session: null, isLoading: false, error: null })),
}));
vi.mock("@/hooks/useAgents", () => ({
  useSessionAgent: vi.fn(() => ({ data: undefined })),
  useCreateMcpServer: () => ({ mutate: vi.fn(), isPending: false, error: null }),
  useUpdateMcpServer: () => ({ mutate: vi.fn(), isPending: false, error: null }),
  useDeleteMcpServer: () => ({ mutate: vi.fn(), isPending: false, error: null }),
}));

// AgentDrawer's own two data hooks — a fixed two-agent roster, same shape
// AgentDrawer.test.tsx uses.
vi.mock("@/hooks/useAvailableAgents", () => ({ useAvailableAgents: vi.fn() }));
vi.mock("@/hooks/useAgentAvatars", () => ({ useAgentAvatars: vi.fn() }));

// See the file banner: Sidebar's own button/prop wiring is Sidebar.test.tsx's
// job. This stub keeps only the one prop AppShell's handler actually needs to
// be exercised through.
vi.mock("./Sidebar", () => ({
  Sidebar: ({ onBrowseAgents }: { onBrowseAgents: () => void }) => (
    <button type="button" data-testid="browse-agents-button" onClick={onBrowseAgents}>
      Agents
    </button>
  ),
}));

import { AppShell } from "./AppShell";
import { useAvailableAgents } from "@/hooks/useAvailableAgents";
import { useAgentAvatars } from "@/hooks/useAgentAvatars";
import { readLastAgentId } from "@/lib/agentPreferences";
import { useSession } from "@/hooks/useSession";

function LocationDisplay() {
  const location = useLocation();
  return <div data-testid="location">{location.pathname + location.search}</div>;
}

function renderShell(path = "/") {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <TooltipProvider>
        <MemoryRouter initialEntries={[path]}>
          <Routes>
            <Route element={<AppShell />}>
              <Route
                index
                element={
                  <>
                    <div>home</div>
                    <LocationDisplay />
                  </>
                }
              />
              <Route
                path="c/:conversationId"
                element={
                  <>
                    <div>page</div>
                    <LocationDisplay />
                  </>
                }
              />
              <Route path="iris" element={<LocationDisplay />} />
              <Route path="iris/:sessionId" element={<LocationDisplay />} />
            </Route>
          </Routes>
        </MemoryRouter>
      </TooltipProvider>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  localStorage.clear();
  vi.mocked(useAvailableAgents).mockReturnValue({
    data: [
      { id: "a1", name: "researcher", display_name: "Researcher", description: "" },
      { id: "a2", name: "coder", display_name: "Coder", description: "" },
      { id: "a3", name: "iris", display_name: "Iris", description: "" },
    ],
  } as never);
  vi.mocked(useAgentAvatars).mockReturnValue({ data: {} } as never);
});

afterEach(() => {
  cleanup();
  localStorage.clear();
});

// The drawer's "Open workspace" is half of the brief's second acceptance
// criterion — her row offers a native chat or her workspace, and **both land in
// the same session**. The mechanism is the ternary in AppShell's
// `onOpenWorkspace`, and until these cases it had no test at all: nothing
// asserted that opening the workspace from inside an Iris session keeps you in
// that session rather than dropping you on the tenant picker to start a second
// one. A claim in a "done" list resting on an untested branch is the thing this
// crew spent the night hunting, so it is tested here even though it is my own.
describe("AppShell: the workspace and the chat are one session", () => {
  function withActiveSession(session: unknown) {
    vi.mocked(useSession).mockReturnValue({
      session,
      isLoading: false,
      error: null,
    } as never);
  }

  it("keeps you in the session you are already in", () => {
    withActiveSession({ id: "conv_iris", agentName: "iris", parentSessionId: null });
    renderShell("/c/conv_iris");
    fireEvent.click(screen.getByTestId("browse-agents-button"));
    fireEvent.click(screen.getByRole("button", { name: "Open Iris workspace" }));
    // Not "/iris" — that is the picker, and it would start a SECOND session,
    // so the workspace would read a different conversation's reports than the
    // chat the user just came from.
    expect(screen.getByTestId("location")).toHaveTextContent("/iris/conv_iris");
  });

  it("falls back to the picker when the open session is not hers", () => {
    withActiveSession({ id: "conv_other", agentName: "claude-native-ui", parentSessionId: null });
    renderShell("/c/conv_other");
    fireEvent.click(screen.getByTestId("browse-agents-button"));
    fireEvent.click(screen.getByRole("button", { name: "Open Iris workspace" }));
    expect(screen.getByTestId("location")).toHaveTextContent("/iris");
    expect(screen.getByTestId("location")).not.toHaveTextContent("/iris/conv_other");
  });

  it("falls back to the picker from the landing screen, where there is no session", () => {
    withActiveSession(null);
    renderShell("/");
    fireEvent.click(screen.getByTestId("browse-agents-button"));
    fireEvent.click(screen.getByRole("button", { name: "Open Iris workspace" }));
    expect(screen.getByTestId("location")).toHaveTextContent("/iris");
  });
});

describe("AppShell agent drawer wiring", () => {
  it("opens the real AgentDrawer from the sidebar trigger, and is reachable from the landing screen (not gated on a conversation)", () => {
    renderShell("/");
    expect(screen.queryByTestId("agent-drawer")).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId("browse-agents-button"));

    expect(screen.getByTestId("agent-drawer")).toBeInTheDocument();
    expect(screen.getByTestId("agent-drawer-row-researcher")).toBeInTheDocument();
  });

  it('persists the pick and hands it to the landing via ?agent=<id> — not a bare navigate("/")', () => {
    renderShell("/");

    fireEvent.click(screen.getByTestId("browse-agents-button"));
    fireEvent.click(screen.getByTestId("agent-drawer-row-coder"));

    // The drawer closes and the preference is persisted (AppShell keeps
    // writeLastAgentId so it survives independently of the URL hand-off).
    expect(screen.queryByTestId("agent-drawer")).not.toBeInTheDocument();
    expect(readLastAgentId()).toBe("a2");

    // The critical bit: navigation carries the agent id in the URL rather
    // than a bare "/" — a bare navigate("/") is exactly the reversed
    // mechanism that silently started the wrong agent, because it relies on
    // a remount that "/" -> "/" never triggers (see NewChatDialog.tsx and
    // its tests for the landing side of this contract).
    expect(screen.getByTestId("location").textContent).toBe("/?agent=a2");
  });

  it("preserves ?project= from a project-scoped landing when handing off to ?agent=", () => {
    renderShell("/?project=Foo");

    fireEvent.click(screen.getByTestId("browse-agents-button"));
    fireEvent.click(screen.getByTestId("agent-drawer-row-coder"));

    // The drawer trigger sits in the same sidebar as the project-scoped
    // landing links (Sidebar.tsx), so a pick made from a project-filtered
    // landing must not drop the project scope — only page-local params
    // (file, comment, view, sidebar) are meant to be dropped, not `project`.
    expect(screen.getByTestId("location").textContent).toBe("/?project=Foo&agent=a2");
  });

  it("closes the drawer when the command palette opens, so ⌘K doesn't paint under its scrim", () => {
    renderShell("/");

    fireEvent.click(screen.getByTestId("browse-agents-button"));
    expect(screen.getByTestId("agent-drawer")).toBeInTheDocument();

    // The palette (components/ui/dialog.tsx, z-50) sits below the drawer's
    // scrim (z-[55]/[56]) — the fix this test covers closes the drawer
    // whenever the palette opens, rather than renumbering the shared dialog
    // z-index every other dialog in the app relies on. isMacPlatform() is
    // false in this test environment (see src/lib/hotkeys.ts), so the
    // non-mac chord is Ctrl+K, not ⌘K.
    fireEvent.keyDown(window, { key: "k", ctrlKey: true });

    expect(screen.queryByTestId("agent-drawer")).not.toBeInTheDocument();
    expect(screen.getByTestId("command-palette-input")).toBeInTheDocument();
  });
});
