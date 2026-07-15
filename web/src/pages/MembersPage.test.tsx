// Tests for the admin MembersPage (invite, password reset, delete user).
//
// Browser e2e is impractical (admin/accounts-gated — would need a second
// authenticated server), so the surface is pinned here by mocking the
// mode-agnostic identity probe (resolveIdentity/getCurrentIsAdmin gate admin),
// accountsApi (listUsers/createInvite/resetUserPassword/deleteUser drive the
// table + actions), and useServerInfo (accounts_enabled toggles the
// manage-vs-read-only surface — the latter is the OIDC case).

import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MembersPage } from "./MembersPage";
import type { AccountListEntry } from "@/lib/accountsApi";
import * as accountsApi from "@/lib/accountsApi";
import * as adminApi from "@/lib/adminApi";
import * as identity from "@/lib/identity";

const mocks = vi.hoisted(() => ({
  accountsEnabled: true,
  loginUrl: null as string | null,
  serverVersion: "0.3.0.dev0" as string | null,
}));

vi.mock("@/lib/CapabilitiesContext", () => ({
  useServerInfo: () => ({
    accounts_enabled: mocks.accountsEnabled,
    login_url: mocks.loginUrl,
    server_version: mocks.serverVersion,
  }),
}));
vi.mock("@/lib/identity", () => ({
  resolveIdentity: vi.fn(),
  getCurrentIsAdmin: vi.fn(),
}));
vi.mock("@/lib/accountsApi", () => ({
  listUsers: vi.fn(),
  createInvite: vi.fn(),
  resetUserPassword: vi.fn(),
  deleteUser: vi.fn(),
}));
// The usage rollup (owned sessions / hosts / tokens / cost) is joined from the
// admin analytics endpoint; default to empty so existing tests are unaffected.
vi.mock("@/lib/adminApi", () => ({ listAllUsers: vi.fn() }));

function user(overrides: Partial<AccountListEntry> = {}): AccountListEntry {
  return {
    id: "bob",
    is_admin: false,
    created_at: null,
    last_login_at: null,
    has_password: true,
    ...overrides,
  };
}

function renderPage() {
  return render(
    <MemoryRouter>
      <MembersPage />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  mocks.accountsEnabled = true;
  mocks.loginUrl = null;
  mocks.serverVersion = "0.3.0.dev0";
  vi.mocked(identity.resolveIdentity).mockResolvedValue("admin");
  vi.mocked(identity.getCurrentIsAdmin).mockReturnValue(true);
  vi.mocked(accountsApi.listUsers).mockResolvedValue([]);
  vi.mocked(accountsApi.createInvite).mockResolvedValue({
    ok: true,
    token: "tok",
    register_url: "https://app.example.com/register?invite=tok",
    expires_at: 9_999_999_999,
    is_admin: false,
  });
  vi.mocked(accountsApi.resetUserPassword).mockResolvedValue({
    ok: true,
    id: "bob",
    new_password: "fresh-pw-123",
  });
  vi.mocked(accountsApi.deleteUser).mockResolvedValue({ ok: true });
  vi.mocked(adminApi.listAllUsers).mockResolvedValue({ users: [], hidden: 0 });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("MembersPage gating", () => {
  it("shows a loading state until the identity probe resolves", () => {
    vi.mocked(identity.resolveIdentity).mockReturnValue(new Promise(() => {})); // never resolves
    renderPage();
    expect(screen.getByText("Loading…")).toBeInTheDocument();
  });

  it("blocks non-admins with a permission message and never lists users", async () => {
    vi.mocked(identity.resolveIdentity).mockResolvedValue("alice");
    vi.mocked(identity.getCurrentIsAdmin).mockReturnValue(false);
    renderPage();
    expect(
      await screen.findByText("You don't have permission to manage members."),
    ).toBeInTheDocument();
    expect(accountsApi.listUsers).not.toHaveBeenCalled();
  });

  it("stays in the loading state for an unauthenticated visitor (resolveIdentity redirects)", async () => {
    // resolveIdentity returns null AND owns the login redirect, so the page
    // just never leaves its loading state — it must NOT list users.
    vi.mocked(identity.resolveIdentity).mockResolvedValue(null);
    renderPage();
    await waitFor(() => expect(identity.getCurrentIsAdmin).not.toHaveBeenCalled());
    expect(accountsApi.listUsers).not.toHaveBeenCalled();
    expect(screen.getByText("Loading…")).toBeInTheDocument();
  });
});

describe("MembersPage table", () => {
  it("renders an empty state when there are no members", async () => {
    renderPage();
    expect(await screen.findByText("No members yet.")).toBeInTheDocument();
  });

  it("lists members with role badges and marks the current admin", async () => {
    vi.mocked(accountsApi.listUsers).mockResolvedValue([
      user({ id: "admin", is_admin: true }),
      user({ id: "bob" }),
    ]);
    renderPage();

    const adminRow = (await screen.findByText("admin")).closest("tr")!;
    expect(within(adminRow).getByText("Admin")).toBeInTheDocument();
    expect(within(adminRow).getByText("(you)")).toBeInTheDocument();

    const bobRow = screen.getByText("bob").closest("tr")!;
    expect(within(bobRow).getByText("Member")).toBeInTheDocument();
  });

  it("disables Remove for the current user and Reset for external (passwordless) users", async () => {
    vi.mocked(accountsApi.listUsers).mockResolvedValue([
      user({ id: "admin", is_admin: true }),
      user({ id: "ext", has_password: false }),
    ]);
    renderPage();

    const adminRow = (await screen.findByText("admin")).closest("tr")!;
    expect(within(adminRow).getByRole("button", { name: /Remove/ })).toBeDisabled();

    const extRow = screen.getByText("ext").closest("tr")!;
    expect(within(extRow).getByRole("button", { name: /Reset/ })).toBeDisabled();
  });

  it("joins the usage rollup onto rows with drill-down links, blanking members with no analytics", async () => {
    vi.mocked(accountsApi.listUsers).mockResolvedValue([
      user({ id: "alice@example.com" }),
      user({ id: "ghost@example.com" }), // invite-only: present here, absent from analytics
    ]);
    vi.mocked(adminApi.listAllUsers).mockResolvedValue({
      users: [
        {
          user_id: "alice@example.com",
          is_admin: false,
          cost_usd: 4.1,
          total_tokens: 1_200_000,
          session_count: 12,
          host_count: 3,
          online_host_count: 2,
        },
      ],
      hidden: 1,
    });
    renderPage();

    const aliceRow = (await screen.findByText("alice@example.com")).closest("tr")!;
    // Owned + Hosts are drill-down links into the Sessions / Hosts sections.
    const owned = within(aliceRow).getByRole("link", { name: "12" });
    expect(owned.getAttribute("href")).toContain("/settings/sessions?user=alice%40example.com");
    const hosts = within(aliceRow).getByRole("link", { name: "3 · 2 online" });
    expect(hosts.getAttribute("href")).toContain("/settings/hosts?user=alice%40example.com");
    expect(within(aliceRow).getByText("1.2M")).toBeInTheDocument();
    expect(within(aliceRow).getByText("$4.10")).toBeInTheDocument();

    // The member with no analytics still lists, with "—" usage cells (no links).
    const ghostRow = screen.getByText("ghost@example.com").closest("tr")!;
    expect(within(ghostRow).queryByRole("link")).toBeNull();
  });
});

describe("MembersPage actions", () => {
  it("resets a user's password and shows the new password once", async () => {
    vi.mocked(accountsApi.listUsers).mockResolvedValue([user({ id: "bob" })]);
    renderPage();

    const bobRow = (await screen.findByText("bob")).closest("tr")!;
    fireEvent.click(within(bobRow).getByRole("button", { name: /Reset/ }));

    await waitFor(() => expect(accountsApi.resetUserPassword).toHaveBeenCalledWith("bob"));
    // The new password renders in a readonly, copyable input.
    expect(await screen.findByDisplayValue("fresh-pw-123")).toBeInTheDocument();
  });

  it("deletes a user through the confirmation dialog and refreshes the list", async () => {
    vi.mocked(accountsApi.listUsers)
      .mockResolvedValueOnce([user({ id: "bob" })])
      .mockResolvedValue([]); // after delete → refresh returns empty
    renderPage();

    const bobRow = (await screen.findByText("bob")).closest("tr")!;
    fireEvent.click(within(bobRow).getByRole("button", { name: /Remove/ }));

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("Remove bob?")).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: /^Remove$/ }));

    await waitFor(() => expect(accountsApi.deleteUser).toHaveBeenCalledWith("bob"));
    expect(await screen.findByText("No members yet.")).toBeInTheDocument();
  });

  it("creates an invite and surfaces the single-use URL", async () => {
    renderPage();
    await screen.findByText("No members yet.");

    fireEvent.click(screen.getByRole("button", { name: /Invite member/ }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: /Create invite/ }));

    await waitFor(() => expect(accountsApi.createInvite).toHaveBeenCalledWith(false));
    // The single-use invite URL renders in a readonly, copyable input.
    expect(await screen.findByDisplayValue(/register\?invite=tok/)).toBeInTheDocument();
  });
});

describe("MembersPage in plain header/single-user mode", () => {
  beforeEach(() => {
    // Single-user mode: no accounts, no IdP (login_url is null). The
    // /auth/users endpoint does not exist, so the page must skip the fetch
    // and show a "not available" message instead.
    mocks.accountsEnabled = false;
    mocks.loginUrl = null;
    mocks.serverVersion = "0.3.0.dev0";
  });

  it("shows a not-available message and never calls listUsers", async () => {
    renderPage();
    expect(
      await screen.findByText("Member management is not available in single-user mode."),
    ).toBeInTheDocument();
    expect(accountsApi.listUsers).not.toHaveBeenCalled();
  });
});

describe("MembersPage under OIDC (read-only)", () => {
  beforeEach(() => {
    // OIDC: accounts disabled but login_url is non-null (IdP present).
    // The list still renders (admins can see who's provisioned), but every
    // management affordance is gone.
    mocks.accountsEnabled = false;
    mocks.loginUrl = "/auth/login";
  });

  it("lists users but offers no management actions", async () => {
    vi.mocked(accountsApi.listUsers).mockResolvedValue([
      user({ id: "admin", is_admin: true }),
      user({ id: "bob" }),
    ]);
    renderPage();

    // The list renders with role badges.
    const bobRow = (await screen.findByText("bob")).closest("tr")!;
    expect(within(bobRow).getByText("Member")).toBeInTheDocument();

    // No invite button, no per-row Reset/Remove, and a read-only notice.
    expect(screen.queryByRole("button", { name: /Invite member/ })).toBeNull();
    expect(within(bobRow).queryByRole("button", { name: /Reset/ })).toBeNull();
    expect(within(bobRow).queryByRole("button", { name: /Remove/ })).toBeNull();
    expect(screen.getByText(/provisioned automatically on first sign-in/)).toBeInTheDocument();
  });
});
