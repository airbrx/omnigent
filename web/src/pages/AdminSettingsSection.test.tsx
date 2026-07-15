// Tests for the shared admin gate + chrome used by the Sessions / Hosts
// settings sections: an admin sees the title + children; a non-admin gets a
// "no access" message and the children never mount.

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AdminSettingsSection } from "./AdminSettingsSection";
import * as identity from "@/lib/identity";

vi.mock("@/lib/identity", () => ({
  resolveIdentity: vi.fn(),
  getCurrentIsAdmin: vi.fn(),
}));

afterEach(cleanup);
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(identity.resolveIdentity).mockResolvedValue("alice@example.com");
});

describe("AdminSettingsSection", () => {
  it("renders the title + children for an admin", async () => {
    vi.mocked(identity.getCurrentIsAdmin).mockReturnValue(true);
    render(
      <AdminSettingsSection title="Hosts">
        <div data-testid="body">table</div>
      </AdminSettingsSection>,
    );
    await waitFor(() => expect(screen.getByTestId("body")).toBeTruthy());
    expect(screen.getByRole("heading", { name: "Hosts" })).toBeTruthy();
  });

  it("blocks a non-admin and never mounts the children", async () => {
    vi.mocked(identity.getCurrentIsAdmin).mockReturnValue(false);
    render(
      <AdminSettingsSection title="Hosts">
        <div data-testid="body">table</div>
      </AdminSettingsSection>,
    );
    await waitFor(() => expect(screen.getByText("You don't have admin access.")).toBeTruthy());
    expect(screen.queryByTestId("body")).toBeNull();
  });
});
