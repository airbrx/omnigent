// Tests for Admin → Users: the user list, host-count column, the
// cross-link hrefs into Sessions/Hosts, and the email filter.

import { cleanup, render, screen, waitFor, within, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { UsersPage } from "./UsersPage";
import * as identity from "@/lib/identity";
import * as adminApi from "@/lib/adminApi";

vi.mock("@/lib/identity", () => ({ getCurrentUserId: vi.fn() }));
vi.mock("@/lib/adminApi", () => ({ listAllUsers: vi.fn() }));

function renderPage() {
  return render(
    <MemoryRouter>
      <UsersPage />
    </MemoryRouter>,
  );
}

describe("UsersPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(identity.getCurrentUserId).mockReturnValue("boss@example.com");
    vi.mocked(adminApi.listAllUsers).mockResolvedValue({
      users: [
        {
          user_id: "alice@example.com",
          is_admin: false,
          cost_usd: 2,
          total_tokens: 1500,
          session_count: 3,
          host_count: 2,
          online_host_count: 1,
        },
      ],
      hidden: 1,
    });
  });
  afterEach(cleanup);

  it("lists users with host count and cross-links to sessions and hosts", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("alice@example.com")).toBeTruthy());

    const row = screen.getByTestId("admin-user-row");
    // Host count uses the live-subset format.
    expect(within(row).getByText("2 · 1 online")).toBeTruthy();
    // Owned count links into Sessions filtered by this user.
    const ownedLink = within(row).getByText("3").closest("a");
    expect(ownedLink?.getAttribute("href")).toContain("/admin/sessions?user=alice%40example.com");
    // Host count links into Hosts filtered by this user.
    const hostLink = within(row).getByText("2 · 1 online").closest("a");
    expect(hostLink?.getAttribute("href")).toContain("/admin/hosts?user=alice%40example.com");
    // Hidden-phantom count surfaced.
    expect(screen.getByText(/1 invite-only account hidden/i)).toBeTruthy();
  });

  it("filters the list by email substring", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("alice@example.com")).toBeTruthy());
    fireEvent.change(screen.getByLabelText(/filter users by email/i), {
      target: { value: "nobody" },
    });
    expect(screen.queryByText("alice@example.com")).toBeNull();
    expect(screen.getByText(/no users match/i)).toBeTruthy();
  });
});
