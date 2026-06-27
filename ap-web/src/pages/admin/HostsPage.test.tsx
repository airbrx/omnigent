// Tests for Admin → Hosts: version + online state, harness summary, the
// status filter, and the row link into a host's sessions.

import { cleanup, render, screen, waitFor, within, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { HostsPage } from "./HostsPage";
import * as adminApi from "@/lib/adminApi";

const navigate = vi.fn();
vi.mock("@/lib/routing", async () => {
  const actual = await vi.importActual<typeof import("@/lib/routing")>("@/lib/routing");
  return { ...actual, useNavigate: () => navigate };
});
vi.mock("@/lib/adminApi", () => ({ listAdminHosts: vi.fn() }));

function renderPage(initial = "/admin/hosts") {
  return render(
    <MemoryRouter initialEntries={[initial]}>
      <HostsPage />
    </MemoryRouter>,
  );
}

describe("HostsPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(adminApi.listAdminHosts).mockResolvedValue([
      {
        host_id: "host_a1",
        name: "alice-laptop",
        owner: "alice@example.com",
        online: true,
        version: "0.3.1",
        harnesses: { "claude-sdk": true, codex: false },
        last_seen: 1_700_000_000,
        created_at: 1_699_000_000,
      },
    ]);
  });
  afterEach(cleanup);

  it("shows version, online state, and ready harnesses", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("alice-laptop")).toBeTruthy());
    const row = screen.getByTestId("admin-host-row");
    expect(within(row).getByText("0.3.1")).toBeTruthy();
    expect(within(row).getByText("online")).toBeTruthy();
    // Only the ready harness (value === true) is listed.
    expect(within(row).getByText("claude-sdk")).toBeTruthy();
  });

  it("clicking a host opens that host's sessions", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("alice-laptop")).toBeTruthy());
    fireEvent.click(screen.getByTestId("admin-host-row"));
    expect(navigate).toHaveBeenCalledWith("/admin/sessions?host=host_a1");
  });

  it("the status filter passes ?status to the API", async () => {
    renderPage();
    await waitFor(() => expect(adminApi.listAdminHosts).toHaveBeenCalled());
    fireEvent.click(screen.getByText("Offline"));
    await waitFor(() =>
      expect(adminApi.listAdminHosts).toHaveBeenLastCalledWith(
        expect.objectContaining({ status: "offline" }),
      ),
    );
  });
});
