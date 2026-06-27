// Tests for Admin → Sessions: URL-driven ?user filter, host column, and
// the filter chip's clear action.

import { cleanup, render, screen, waitFor, within, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SessionsPage } from "./SessionsPage";
import * as adminApi from "@/lib/adminApi";

vi.mock("@/lib/routing", async () => {
  const actual = await vi.importActual<typeof import("@/lib/routing")>("@/lib/routing");
  return { ...actual, useNavigate: () => vi.fn() };
});
vi.mock("@/lib/adminApi", () => ({ listSessions: vi.fn() }));

function renderPage(initial: string) {
  return render(
    <MemoryRouter initialEntries={[initial]}>
      <SessionsPage />
    </MemoryRouter>,
  );
}

describe("SessionsPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(adminApi.listSessions).mockResolvedValue([
      {
        id: "conv_a",
        title: "Alice's session",
        created_at: 1,
        updated_at: 2,
        cost_usd: 2.5,
        total_tokens: 4200,
        role: "owner",
        owner: "alice@example.com",
        is_owner: true,
        host: "alice-laptop",
        host_online: true,
      },
    ]);
  });
  afterEach(cleanup);

  it("loads with the ?user filter and renders the session + host", async () => {
    renderPage("/admin/sessions?user=alice@example.com");
    await waitFor(() =>
      expect(adminApi.listSessions).toHaveBeenCalledWith(
        expect.objectContaining({ user: "alice@example.com" }),
      ),
    );
    await waitFor(() => expect(screen.getByText("Alice's session")).toBeTruthy());
    const row = screen.getByTestId("admin-session-row");
    expect(within(row).getByText("alice-laptop")).toBeTruthy();
    expect(within(row).getByText("$2.50")).toBeTruthy();
    // The active user filter is shown as a clearable chip.
    expect(screen.getByLabelText("Clear user filter")).toBeTruthy();
  });

  it("clearing the user chip drops the filter and reloads", async () => {
    renderPage("/admin/sessions?user=alice@example.com");
    await waitFor(() => expect(screen.getByText("Alice's session")).toBeTruthy());
    fireEvent.click(screen.getByLabelText("Clear user filter"));
    await waitFor(() =>
      expect(adminApi.listSessions).toHaveBeenLastCalledWith(
        expect.not.objectContaining({ user: "alice@example.com" }),
      ),
    );
  });
});
