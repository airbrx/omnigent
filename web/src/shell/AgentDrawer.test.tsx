import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AgentDrawer } from "./AgentDrawer";

vi.mock("@/hooks/useAvailableAgents", () => ({ useAvailableAgents: vi.fn() }));
vi.mock("@/hooks/useAgentAvatars", () => ({ useAgentAvatars: vi.fn() }));

import { useAgentAvatars } from "@/hooks/useAgentAvatars";
import { useAvailableAgents } from "@/hooks/useAvailableAgents";

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
