import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AgentDrawer } from "./AgentDrawer";

vi.mock("@/hooks/useAvailableAgents", () => ({ useAvailableAgents: vi.fn() }));
vi.mock("@/hooks/useAgentAvatars", () => ({ useAgentAvatars: vi.fn() }));
vi.mock("@/lib/host", () => ({ getOmnigentHostConfig: vi.fn(() => ({})) }));
vi.mock("@/lib/identity", () => ({ authenticatedFetch: vi.fn() }));

import { useAgentAvatars } from "@/hooks/useAgentAvatars";
import { useAvailableAgents } from "@/hooks/useAvailableAgents";
import { getOmnigentHostConfig } from "@/lib/host";
import { authenticatedFetch } from "@/lib/identity";

function renderDrawer(onSelectAgent = vi.fn(), open = true, onClose = vi.fn()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <AgentDrawer open={open} onClose={onClose} onSelectAgent={onSelectAgent} />
    </QueryClientProvider>,
  );
  return { onSelectAgent, onClose };
}

beforeEach(() => {
  vi.mocked(useAvailableAgents).mockReturnValue({
    data: [
      { id: "a1", name: "researcher", display_name: "Researcher", description: "Reads things" },
      { id: "a2", name: "cache cow", display_name: "", description: "Harvests leads" },
    ],
  } as never);
  vi.mocked(useAgentAvatars).mockReturnValue({ data: {} } as never);
  vi.mocked(getOmnigentHostConfig).mockReturnValue({} as never);
});

describe("AgentDrawer", () => {
  it("lists every agent with its description", () => {
    renderDrawer();
    expect(screen.getByText("Researcher")).toBeInTheDocument();
    expect(screen.getByText("Reads things")).toBeInTheDocument();
    expect(screen.getByText("Harvests leads")).toBeInTheDocument();
  });

  it("falls back to name when display_name is empty", () => {
    renderDrawer();
    expect(screen.getByText("cache cow")).toBeInTheDocument();
  });

  it("renders initials when an agent has no avatar", () => {
    renderDrawer();
    expect(screen.getByText("RE")).toBeInTheDocument();
    expect(screen.getByText("CC")).toBeInTheDocument();
  });

  it("renders the image when an agent has an avatar", () => {
    vi.mocked(useAgentAvatars).mockReturnValue({
      data: { researcher: "/v1/agent-avatars/researcher?v=1" },
    } as never);
    renderDrawer();
    const img = screen.getByAltText("researcher");
    expect(img).toHaveAttribute("src", "/v1/agent-avatars/researcher?v=1");
    expect(screen.queryByText("RE")).not.toBeInTheDocument();
  });

  it("falls back to initials when the avatar image fails to load", () => {
    vi.mocked(useAgentAvatars).mockReturnValue({
      data: { researcher: "/v1/agent-avatars/researcher?v=1" },
    } as never);
    renderDrawer();
    const img = screen.getByAltText("researcher");
    fireEvent.error(img);
    expect(screen.queryByAltText("researcher")).not.toBeInTheDocument();
    expect(screen.getByText("RE")).toBeInTheDocument();
  });

  it("calls onSelectAgent with the full agent when a row is clicked", () => {
    const { onSelectAgent } = renderDrawer();
    fireEvent.click(screen.getByTestId("agent-drawer-row-researcher"));
    expect(onSelectAgent).toHaveBeenCalledWith({
      id: "a1",
      name: "researcher",
      display_name: "Researcher",
      description: "Reads things",
    });
  });

  it("renders nothing when closed", () => {
    renderDrawer(vi.fn(), false);
    expect(screen.queryByTestId("agent-drawer")).not.toBeInTheDocument();
  });

  it("has an accessible close control that calls onClose", () => {
    const { onClose } = renderDrawer();
    fireEvent.click(screen.getByRole("button", { name: /close/i }));
    expect(onClose).toHaveBeenCalled();
  });

  it("closes on Escape", () => {
    const { onClose } = renderDrawer();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalled();
  });

  it("closes when the scrim is clicked", () => {
    const { onClose } = renderDrawer();
    fireEvent.click(screen.getByTestId("agent-drawer-scrim"));
    expect(onClose).toHaveBeenCalled();
  });
});

it("keeps Iris chat and workspace as separate accessible actions", () => {
  vi.mocked(useAvailableAgents).mockReturnValue({
    data: [
      { id: "iris-id", name: "iris", display_name: "Iris", description: "Read-only cache analyst" },
    ],
  } as never);
  const chat = vi.fn(),
    workspace = vi.fn();
  const client = new QueryClient();
  render(
    <QueryClientProvider client={client}>
      <AgentDrawer open onClose={vi.fn()} onSelectAgent={chat} onOpenWorkspace={workspace} />
    </QueryClientProvider>,
  );
  fireEvent.click(screen.getByTestId("agent-drawer-row-iris"));
  expect(chat).toHaveBeenCalledWith(expect.objectContaining({ id: "iris-id" }));
  // Named for a screen reader reading the roster's controls out of context,
  // where a bare "Open workspace" says nothing about whose.
  fireEvent.click(screen.getByRole("button", { name: "Open Iris workspace" }));
  expect(workspace).toHaveBeenCalledOnce();
  expect(workspace).toHaveBeenCalledWith("iris");
});

it("gives Eva her own workspace action and her packaged portrait", () => {
  vi.mocked(useAvailableAgents).mockReturnValue({
    data: [
      { id: "eva-id", name: "eva", display_name: "Eva", description: "Works the lead list" },
      { id: "a1", name: "researcher", display_name: "Researcher", description: "Reads things" },
    ],
  } as never);
  const workspace = vi.fn();
  const client = new QueryClient();
  render(
    <QueryClientProvider client={client}>
      <AgentDrawer open onClose={vi.fn()} onSelectAgent={vi.fn()} onOpenWorkspace={workspace} />
    </QueryClientProvider>,
  );
  fireEvent.click(screen.getByRole("button", { name: "Open Eva workspace" }));
  expect(workspace).toHaveBeenCalledWith("eva");
  expect(screen.getByAltText("eva")).toHaveAttribute("src", "/v1/eva/portrait");
  // Only agents that have a workspace get the button.
  expect(screen.queryByRole("button", { name: /Open Researcher workspace/ })).toBeNull();
});

it("opens with focus on the way out, and traps Tab inside the drawer", () => {
  renderDrawer();
  // Opening on Close, not on the header's first control: the drawer is a
  // detour, and the escape hatch is what should be under the cursor.
  expect(screen.getByRole("button", { name: "Close" })).toHaveFocus();
  const first = screen.getByTestId("agent-drawer-edit-avatars");
  const last = screen.getByTestId("agent-drawer-row-cache cow");
  first.focus();
  fireEvent.keyDown(window, { key: "Tab", shiftKey: true });
  expect(last).toHaveFocus();
  fireEvent.keyDown(window, { key: "Tab" });
  expect(first).toHaveFocus();
});

// Deferred from #16: embedded, the host proxies the API behind a path prefix
// and cookie+CSRF auth a browser's own <img> GET cannot carry, so every avatar
// silently degraded to initials in the build:embed target.
describe("AgentDrawer avatars in the embedded target", () => {
  const objectUrls: string[] = [];
  const revoked: string[] = [];

  beforeEach(() => {
    objectUrls.length = 0;
    revoked.length = 0;
    // jsdom implements neither, and the component's whole point here is that
    // it renders fetched bytes rather than a URL the browser resolves itself.
    URL.createObjectURL = vi.fn((blob: Blob) => {
      const url = `blob:agent-avatar-${objectUrls.length}-${blob.size}`;
      objectUrls.push(url);
      return url;
    }) as never;
    URL.revokeObjectURL = vi.fn((url: string) => {
      revoked.push(url);
    }) as never;
    vi.mocked(getOmnigentHostConfig).mockReturnValue({ fetcher: vi.fn() } as never);
    vi.mocked(useAgentAvatars).mockReturnValue({
      data: { researcher: "/v1/agent-avatars/researcher?v=1" },
    } as never);
  });

  afterEach(() => {
    vi.mocked(authenticatedFetch).mockReset();
  });

  it("pulls the bytes through the host fetcher instead of a bare <img> src", async () => {
    vi.mocked(authenticatedFetch).mockResolvedValue(
      // A string body, not a `new Blob(...)`. jsdom installs its own Blob, and
      // under the Node the CI runner uses it has no `.stream()`, so passing one
      // to `Response` throws "object.stream is not a function". It happens to
      // work on a newer local Node, which is exactly the kind of test that
      // passes for its author and fails for everyone else. `.blob()` on the
      // response still returns a real Blob, which is what the component reads.
      new Response("avatar-bytes", { headers: { "Content-Type": "image/png" } }),
    );
    renderDrawer();
    const img = await screen.findByAltText("researcher");
    expect(authenticatedFetch).toHaveBeenCalledWith("/v1/agent-avatars/researcher?v=1");
    // The point of the fix: never the raw URL, which embedded resolves against
    // the wrong origin and without the host's auth.
    expect(img.getAttribute("src")).toBe(objectUrls[0]);
    expect(img.getAttribute("src")).not.toBe("/v1/agent-avatars/researcher?v=1");
  });

  it("degrades to the initials chip when the fetched avatar is refused", async () => {
    vi.mocked(authenticatedFetch).mockResolvedValue(new Response("nope", { status: 404 }));
    renderDrawer();
    await waitFor(() => expect(screen.getByText("RE")).toBeInTheDocument());
    expect(screen.queryByAltText("researcher")).not.toBeInTheDocument();
  });

  it("releases the object URL when the drawer unmounts", async () => {
    vi.mocked(authenticatedFetch).mockResolvedValue(
      // A string body, not a `new Blob(...)`. jsdom installs its own Blob, and
      // under the Node the CI runner uses it has no `.stream()`, so passing one
      // to `Response` throws "object.stream is not a function". It happens to
      // work on a newer local Node, which is exactly the kind of test that
      // passes for its author and fails for everyone else. `.blob()` on the
      // response still returns a real Blob, which is what the component reads.
      new Response("avatar-bytes", { headers: { "Content-Type": "image/png" } }),
    );
    const { unmount } = render(
      <QueryClientProvider client={new QueryClient()}>
        <AgentDrawer open onClose={vi.fn()} onSelectAgent={vi.fn()} />
      </QueryClientProvider>,
    );
    await screen.findByAltText("researcher");
    unmount();
    expect(revoked).toEqual([objectUrls[0]]);
  });
});

// Requirement from the brief: Iris appears in the drawer *with her portrait*.
// Nothing uploads to the avatar store, so without the host route she would be
// the one agent in the roster reduced to grey initials.
describe("Iris's portrait", () => {
  beforeEach(() => {
    vi.mocked(useAvailableAgents).mockReturnValue({
      data: [
        { id: "iris-id", name: "iris", display_name: "Iris", description: "Cache analyst" },
        { id: "a1", name: "researcher", display_name: "Researcher", description: "Reads things" },
      ],
    } as never);
  });

  it("falls back to the portrait her pinned package ships", () => {
    renderDrawer();
    expect(screen.getByAltText("iris")).toHaveAttribute("src", "/v1/iris/portrait");
    // The fallback is hers alone: no other agent has a packaged portrait, and
    // inventing a URL for one would produce a broken image, not a picture.
    expect(screen.queryByAltText("researcher")).not.toBeInTheDocument();
    expect(screen.getByText("RE")).toBeInTheDocument();
  });

  it("prefers an uploaded avatar over the packaged portrait", () => {
    vi.mocked(useAgentAvatars).mockReturnValue({
      data: { iris: "/v1/agent-avatars/iris?v=7" },
    } as never);
    renderDrawer();
    expect(screen.getByAltText("iris")).toHaveAttribute("src", "/v1/agent-avatars/iris?v=7");
  });

  it("degrades to initials on a host with no Iris package", () => {
    renderDrawer();
    fireEvent.error(screen.getByAltText("iris"));
    expect(screen.queryByAltText("iris")).not.toBeInTheDocument();
    expect(screen.getByText("IR")).toBeInTheDocument();
  });
});

// Deferred from #16: the avatar API shipped with the drawer and nothing could
// reach it, so every agent without a packaged portrait sat on initials forever.
describe("setting an agent's picture", () => {
  function openEditing() {
    // Calls accumulate across this file's tests; only the ones this drawer
    // makes are this test's evidence.
    vi.mocked(authenticatedFetch).mockClear();
    renderDrawer();
    fireEvent.click(screen.getByTestId("agent-drawer-edit-avatars"));
  }

  it("keeps the housekeeping controls out of the way until asked for", () => {
    renderDrawer();
    expect(screen.queryByRole("button", { name: "Change researcher's picture" })).toBeNull();
    fireEvent.click(screen.getByTestId("agent-drawer-edit-avatars"));
    expect(screen.getByRole("button", { name: "Change researcher's picture" })).toBeInTheDocument();
  });

  it("uploads the picked file to the agent's own endpoint", async () => {
    vi.mocked(authenticatedFetch).mockResolvedValue(
      new Response(JSON.stringify({ agent_name: "researcher", updated_at: 2 })),
    );
    openEditing();
    fireEvent.change(screen.getByTestId("agent-avatar-file-researcher"), {
      target: { files: [new File(["png-bytes"], "face.png", { type: "image/png" })] },
    });
    await waitFor(() => expect(screen.getByText("Picture saved.")).toBeInTheDocument());
    const [path, options] = vi.mocked(authenticatedFetch).mock.calls[0];
    expect(path).toBe("/v1/agent-avatars/researcher");
    expect(options?.method).toBe("PUT");
    const body = options?.body as FormData;
    expect((body.get("file") as File).name).toBe("face.png");
  });

  it("reports the host's own reason for a refusal, not a generic failure", async () => {
    vi.mocked(authenticatedFetch).mockResolvedValue(
      new Response(
        JSON.stringify({
          error: { code: "invalid_input", message: "unsupported content type 'image/svg+xml'" },
        }),
        { status: 400 },
      ),
    );
    openEditing();
    fireEvent.change(screen.getByTestId("agent-avatar-file-researcher"), {
      target: { files: [new File(["<svg/>"], "face.svg", { type: "image/svg+xml" })] },
    });
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("unsupported content type 'image/svg+xml'");
  });

  it("offers removal only for an agent that actually has a stored picture", async () => {
    vi.mocked(useAgentAvatars).mockReturnValue({
      data: { researcher: "/v1/agent-avatars/researcher?v=1" },
    } as never);
    vi.mocked(authenticatedFetch).mockResolvedValue(new Response(null, { status: 204 }));
    openEditing();
    expect(screen.queryByRole("button", { name: "Remove cache cow's picture" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Remove researcher's picture" }));
    await waitFor(() => expect(screen.getByText("Picture removed.")).toBeInTheDocument());
    const [path, options] = vi.mocked(authenticatedFetch).mock.calls[0];
    expect(path).toBe("/v1/agent-avatars/researcher");
    expect(options?.method).toBe("DELETE");
  });

  it("says the host was unreachable rather than pretending the save worked", async () => {
    vi.mocked(authenticatedFetch).mockRejectedValue(new Error("offline"));
    openEditing();
    fireEvent.change(screen.getByTestId("agent-avatar-file-researcher"), {
      target: { files: [new File(["png-bytes"], "face.png", { type: "image/png" })] },
    });
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("the host could not be reached");
  });
});

// Abram: "custom agents like the Cache Cow and Iris in a menu and the coding
// CLIs in another area instead of one big long list."
describe("grouping the roster", () => {
  const ROSTER = [
    { id: "i", name: "iris", display_name: "Iris", description: "Cache analyst", builtin: false },
    { id: "c", name: "cache cow", display_name: "Cache Cow", description: "Leads", builtin: false },
    {
      id: "cc",
      name: "claude-native-ui",
      display_name: "Claude Code",
      description: "",
      builtin: true,
    },
    { id: "cx", name: "codex-native-ui", display_name: "Codex", description: "", builtin: true },
  ];

  function headings() {
    return screen.getAllByRole("heading", { level: 3 }).map((h) => h.textContent);
  }

  it("separates agents picked for what they know from harnesses picked for what they do", () => {
    vi.mocked(useAvailableAgents).mockReturnValue({ data: ROSTER } as never);
    renderDrawer();
    expect(headings()).toEqual(["Custom agents", "Coding CLIs"]);

    const custom = screen.getByRole("region", { name: "Custom agents" });
    const clis = screen.getByRole("region", { name: "Coding CLIs" });
    expect(custom).toContainElement(screen.getByTestId("agent-drawer-row-iris"));
    expect(custom).toContainElement(screen.getByTestId("agent-drawer-row-cache cow"));
    expect(clis).toContainElement(screen.getByTestId("agent-drawer-row-claude-native-ui"));
    expect(clis).toContainElement(screen.getByTestId("agent-drawer-row-codex-native-ui"));
  });

  it("puts the custom agents first, which is the half this drawer exists to surface", () => {
    vi.mocked(useAvailableAgents).mockReturnValue({ data: ROSTER } as never);
    renderDrawer();
    // A deliberate divergence from the other pickers, so assert it rather than
    // letting a later refactor quietly reorder it back.
    expect(headings()[0]).toBe("Custom agents");
  });

  it("keeps Iris's portrait through the grouping", () => {
    vi.mocked(useAvailableAgents).mockReturnValue({ data: ROSTER } as never);
    renderDrawer();
    expect(screen.getByRole("region", { name: "Custom agents" })).toContainElement(
      screen.getByAltText("iris"),
    );
  });

  it("does not offer an agent the composer would refuse to start", () => {
    // `nessie` is superseded and the bare `kimi` harnesses are headless; the
    // other pickers already hide them, and a click here starts a session.
    vi.mocked(useAvailableAgents).mockReturnValue({
      data: [...ROSTER, { id: "n", name: "nessie", display_name: "Nessie", description: "" }],
    } as never);
    renderDrawer();
    expect(screen.queryByTestId("agent-drawer-row-nessie")).toBeNull();
  });

  it("renders no heading for a group with nothing in it", () => {
    vi.mocked(useAvailableAgents).mockReturnValue({
      data: ROSTER.filter((a) => a.builtin === false),
    } as never);
    renderDrawer();
    expect(headings()).toEqual(["Custom agents"]);
  });
});
