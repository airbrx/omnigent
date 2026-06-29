// Tests for the admin layout: the is_admin gate, the tab bar, and the
// server-version line.

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AdminLayout } from "./AdminLayout";
import * as identity from "@/lib/identity";
import * as adminApi from "@/lib/adminApi";

vi.mock("@/lib/identity", () => ({
  resolveIdentity: vi.fn(),
  getCurrentIsAdmin: vi.fn(),
}));
vi.mock("@/lib/adminApi", () => ({ getServerInfo: vi.fn() }));

function renderLayout() {
  return render(
    <MemoryRouter initialEntries={["/admin/users"]}>
      <Routes>
        <Route path="/admin" element={<AdminLayout />}>
          <Route path="users" element={<div>child content</div>} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

describe("AdminLayout", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(identity.resolveIdentity).mockResolvedValue("boss@example.com");
  });
  afterEach(cleanup);

  it("shows a no-access message for non-admins", async () => {
    vi.mocked(identity.getCurrentIsAdmin).mockReturnValue(false);
    renderLayout();
    await waitFor(() => expect(screen.getByText(/don't have admin access/i)).toBeTruthy());
    expect(adminApi.getServerInfo).not.toHaveBeenCalled();
  });

  it("renders tabs, the server version, and the child outlet for an admin", async () => {
    vi.mocked(identity.getCurrentIsAdmin).mockReturnValue(true);
    vi.mocked(adminApi.getServerInfo).mockResolvedValue({
      version: "0.3.0.dev0",
      commit: "6fdc4b8c1234",
      built_at: 1_700_000_000,
      version_label: "0.3.0.dev0 (6fdc4b8c)",
      install_command: "curl -fsSL https://omnigent.example/install.sh | sh",
    });
    renderLayout();
    await waitFor(() => expect(screen.getByText("child content")).toBeTruthy());
    // Tabs.
    expect(screen.getByRole("link", { name: "Users" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "Sessions" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "Hosts" })).toBeTruthy();
    // Server version line (commit truncated to 8 chars).
    await waitFor(() => expect(screen.getByText(/0\.3\.0\.dev0 \(6fdc4b8c/)).toBeTruthy());
  });
});
