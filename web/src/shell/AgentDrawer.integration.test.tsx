import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { AgentDrawer } from "./AgentDrawer";
import type { AvailableAgent } from "@/hooks/useAvailableAgents";

vi.mock("@/hooks/useAvailableAgents", () => ({
  useAvailableAgents: () => ({
    data: [{ id: "a1", name: "researcher", display_name: "Researcher", description: "" }],
  }),
}));
vi.mock("@/hooks/useAgentAvatars", () => ({ useAgentAvatars: () => ({ data: {} }) }));

// Mirrors the AppShell wiring: a trigger toggles `open`, selecting a row
// closes the drawer and hands the full agent to the caller, which is what
// AppShell needs to seed the new-chat landing's stored agent pick.
function Harness({ onSelect }: { onSelect: (agent: AvailableAgent) => void }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" data-testid="browse-agents-button" onClick={() => setOpen(true)}>
        Agents
      </button>
      <AgentDrawer
        open={open}
        onClose={() => setOpen(false)}
        onSelectAgent={(agent) => {
          setOpen(false);
          onSelect(agent);
        }}
      />
    </>
  );
}

describe("agent drawer wiring", () => {
  it("opens from the trigger and closes on select, reporting the full agent", () => {
    const onSelect = vi.fn();
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <Harness onSelect={onSelect} />
      </QueryClientProvider>,
    );

    expect(screen.queryByTestId("agent-drawer")).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId("browse-agents-button"));
    expect(screen.getByTestId("agent-drawer")).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("agent-drawer-row-researcher"));
    expect(onSelect).toHaveBeenCalledWith({
      id: "a1",
      name: "researcher",
      display_name: "Researcher",
      description: "",
    });
    expect(screen.queryByTestId("agent-drawer")).not.toBeInTheDocument();
  });

  it("closes when the scrim is clicked", () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <Harness onSelect={vi.fn()} />
      </QueryClientProvider>,
    );
    fireEvent.click(screen.getByTestId("browse-agents-button"));
    fireEvent.click(screen.getByTestId("agent-drawer-scrim"));
    expect(screen.queryByTestId("agent-drawer")).not.toBeInTheDocument();
  });
});
